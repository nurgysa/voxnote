# Google Drive Phase 7.0 — Auth + Settings UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Phase 7.0 of the Google Drive backup feature — a working OAuth login flow with cached tokens and a Settings-dialog section showing connection status. After 7.0 users can sign in and out; no backup functionality yet (that's 7.1).

**Architecture:** New `gdrive/` package with a single `auth.py` module that wraps `google-auth-oauthlib`'s `InstalledAppFlow` for the desktop OAuth dance, persists `Credentials` to `~/.audio-transcriber/gdrive-token.json`, and exposes `sign_in()` / `sign_out()` / `is_signed_in()` / `get_account_email()`. Settings dialog grows a "Google Drive" section: status badge + Войти/Выйти buttons. Sign-in runs in a worker thread (browser blocks). All Drive scopes are `drive.file` (non-sensitive — app only sees files it created, no Google verification needed).

**Tech Stack:** Python 3.10, `google-auth==2.43.0`, `google-auth-oauthlib==1.3.0`, `google-api-python-client==2.197.0` (transitive, used in 7.1+), customtkinter (existing), pytest, ruff. No GUI changes to existing dialogs apart from one new section.

**Spec:** `docs/superpowers/specs/2026-04-30-gdrive-backup-design.md` (commit `bbfa10f`).

---

## Pre-flight (do once before starting)

These steps happen OUTSIDE the codebase and gate end-to-end manual testing. Unit tests don't need them — they mock the Google client.

- [ ] **GCP project**: in [console.cloud.google.com](https://console.cloud.google.com/), create a new project `audio-transcriber-personal` (or reuse one).
- [ ] **Enable Drive API**: APIs & Services → Library → Google Drive API → Enable.
- [ ] **OAuth consent screen**: APIs & Services → OAuth consent screen → External, fill in app name, support email, developer email. Add scope `https://www.googleapis.com/auth/drive.file`. Add yourself as a test user (publishing is optional — testing mode is enough for personal use).
- [ ] **OAuth client**: APIs & Services → Credentials → Create Credentials → OAuth client ID → Desktop application → name it `audio-transcriber-desktop`. Download the JSON, note `client_id` and `client_secret` — these are PUBLIC for installed apps (per OAuth 2.0 RFC 8252).
- [ ] **Stash creds locally** in a notes file (e.g. `~/Documents/gdrive-creds.txt`). They'll be pasted into `gdrive/auth.py` in Task A.3 Step 3.

- [ ] Confirm baseline tests green: `pytest -q` → 342 passed. (CLAUDE.md current baseline.)
- [ ] Confirm ruff clean: `python -m ruff check .` → exit 0.

## File map

| PR | File | Change | Estimated LOC |
|---|---|---|---|
| A | `requirements.txt` | Add 3 pins | +3 |
| A | `gdrive/__init__.py` | NEW — empty package marker | ~3 |
| A | `gdrive/auth.py` | NEW — `GDriveAuth` class | ~150 |
| A | `tests/test_gdrive_auth.py` | NEW — 8 mock-based tests | ~150 |
| B | `ui/dialogs/settings.py` | Add `_build_gdrive_section` + sign-in worker | ~90 |
| B | `ui/app/__init__.py` | Add `_gdrive_*` StringVar instances + `_on_gdrive_*_changed` callbacks | ~25 |
| B | `config.example.json` | Add `gdrive_*` config keys | ~6 |
| B | `tests/test_ui_constants.py` (or new `tests/test_settings_gdrive.py`) | Smoke test: GDrive section renders | ~30 |
| B | `CLAUDE.md` | Add Phase 7.0 closure bullet to Active work | +12 |

**Total**: ~470 LOC across 2 PRs (~300 production + ~170 tests/docs).

## Branch strategy

Per CLAUDE.md memory `feedback_stacked_pr_squash_merge.md`: serialize via main, no stacked PRs.

```
main
 ├── feat/gdrive-phase-7.0-auth          → PR-A (auth module + tests)
 │
 main (after PR-A merges)
 ├── feat/gdrive-phase-7.0-settings-ui   → PR-B (Settings dialog integration)
 │
 main (after PR-B merges)
 ├── docs/claude-md-after-gdrive-7.0     → docs follow-up
```

---

## PR-A: `gdrive/auth.py` foundation

**Branch:** `feat/gdrive-phase-7.0-auth` (from `main`).

**Goal:** Ship the `gdrive.auth.GDriveAuth` class as a pure, tested module. No UI yet — that's PR-B. The class exposes everything the UI will need: `sign_in()`, `sign_out()`, `is_signed_in()`, `get_account_email()`, `get_credentials()`.

**Pre-task:**

```bash
git checkout main && git pull --ff-only origin main
git checkout -b feat/gdrive-phase-7.0-auth
```

---

### Task A.1: Pin the 3 Google libraries in `requirements.txt`

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Append new pins**

Open `requirements.txt`. Append at the end (after `numpy==2.2.6`):

```
google-auth==2.43.0
google-auth-oauthlib==1.3.0
google-api-python-client==2.197.0
```

CLAUDE.md invariant #6: "Don't bump versions casually." We're ADDING new pins, not bumping — these three have no overlap with existing deps. Versions taken verbatim from the Phase 7 spec (`docs/superpowers/specs/2026-04-30-gdrive-backup-design.md` line 209-213).

- [ ] **Step 2: Install locally and verify import**

```
pip install -r requirements.txt
python -c "from google_auth_oauthlib.flow import InstalledAppFlow; from google.oauth2.credentials import Credentials; print('ok')"
```

Expected output: `ok`.

If install fails on `google-api-python-client==2.197.0` (rare — that version may have been yanked), check PyPI for the closest available 2.197.x release and use it. Document the change in the commit message.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "$(cat <<'EOF'
deps: add google-auth + oauthlib + api-client for Phase 7.0

Three new pins for the Google Drive backup feature (Phase 7.0 auth).
Versions taken verbatim from the spec at
docs/superpowers/specs/2026-04-30-gdrive-backup-design.md line 209-213.

No existing dep touched (per CLAUDE.md invariant #6 — never liberalize
existing pins). Pure addition.
EOF
)"
```

---

### Task A.2: Create `gdrive/__init__.py` + first failing test

**Files:**
- Create: `gdrive/__init__.py`
- Create: `tests/test_gdrive_auth.py`

- [ ] **Step 1: Create empty package marker**

```python
# gdrive/__init__.py
"""Google Drive integration package.

Phase 7.0 — auth only (this module).
Phase 7.1+ — backup, restore, scheduler, sync.

See docs/superpowers/specs/2026-04-30-gdrive-backup-design.md.
"""
```

- [ ] **Step 2: Write the first failing test (token file location)**

Create `tests/test_gdrive_auth.py`:

```python
"""Tests for gdrive.auth.GDriveAuth — Phase 7.0.

Pure module — no real OAuth, no real network. Stubs:
  - InstalledAppFlow via patching gdrive.auth.InstalledAppFlow
  - Credentials roundtrip via tmp_path
  - Token refresh via MagicMock on Credentials

Pattern mirrors tests/test_tasks_openrouter_client.py (mock the network
boundary, exercise the logic).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gdrive.auth import GDriveAuth, TOKEN_FILENAME


def test_token_path_under_user_home(tmp_path, monkeypatch):
    """Token file lives at ~/.audio-transcriber/gdrive-token.json
    by default. We use tmp_path as a fake home for isolation."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path))   # Windows
    monkeypatch.setenv("HOME", str(tmp_path))          # POSIX

    auth = GDriveAuth()
    assert auth.token_path == tmp_path / ".audio-transcriber" / TOKEN_FILENAME
```

- [ ] **Step 3: Run test to verify it fails**

```
pytest tests/test_gdrive_auth.py::test_token_path_under_user_home -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'gdrive.auth'`.

- [ ] **Step 4: Create the minimal `gdrive/auth.py` stub**

Create `gdrive/auth.py`:

```python
"""Google Drive OAuth + token persistence (Phase 7.0).

Wraps google_auth_oauthlib's InstalledAppFlow to do the desktop OAuth
loopback dance (RFC 8252), persists the resulting Credentials to
~/.audio-transcriber/gdrive-token.json, and exposes sign-in / sign-out
/ is-signed-in for the Settings UI.

Scope is drive.file (non-sensitive — app only sees files it created),
which means no Google manual app verification is required.

Token file lives OUTSIDE config.json by design: config.json itself gets
backed up to Drive, and storing the Drive auth INSIDE that config would
be chicken/egg. See spec line 184-186.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Public OAuth client credentials for the audio-transcriber-desktop GCP project.
# Per RFC 8252, installed-app client secrets are NOT secrets — they ship
# in the binary. Security comes from per-user consent + restricted scope.
# Created in Pre-flight Step 4; paste real values here before manual smoke test.
CLIENT_ID = "REPLACE_WITH_REAL_CLIENT_ID.apps.googleusercontent.com"
CLIENT_SECRET = "REPLACE_WITH_REAL_CLIENT_SECRET"

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

TOKEN_FILENAME = "gdrive-token.json"
APP_DIR_NAME = ".audio-transcriber"


def _default_token_path() -> Path:
    """Default token-cache path: ~/.audio-transcriber/gdrive-token.json.

    Honours USERPROFILE on Windows (where Path.home() also resolves it,
    but explicit env-var fallback is friendlier to tests using monkeypatch).
    """
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".")
    return home / APP_DIR_NAME / TOKEN_FILENAME


class GDriveAuth:
    """Holds the OAuth state for the running session.

    One instance per App. Constructor doesn't touch disk; call load_tokens()
    explicitly if you need cached creds at startup.
    """

    def __init__(self, token_path: Optional[Path] = None) -> None:
        self.token_path = token_path or _default_token_path()
        self._credentials = None       # populated by load_tokens / sign_in
        self._account_email = None     # populated by sign_in (from id_token)
```

- [ ] **Step 5: Run test to verify it passes**

```
pytest tests/test_gdrive_auth.py::test_token_path_under_user_home -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gdrive/__init__.py gdrive/auth.py tests/test_gdrive_auth.py
git commit -m "$(cat <<'EOF'
feat(gdrive/auth): package skeleton + token-path resolution

Phase 7.0 foundation. New `gdrive/` package with `auth.py` exposing a
GDriveAuth class. First slice covers only the token-path resolution
(~/.audio-transcriber/gdrive-token.json) and the constructor.

CLIENT_ID / CLIENT_SECRET are placeholders for now — they get real
values from the GCP OAuth client created in Pre-flight, pasted before
manual smoke test. Unit tests mock InstalledAppFlow so they don't
need real creds.

First test verifies the path defaults to ~/.audio-transcriber on both
Windows (USERPROFILE) and POSIX (HOME).
EOF
)"
```

---

### Task A.3: Implement `sign_in()` via `InstalledAppFlow.run_local_server`

**Files:**
- Modify: `gdrive/auth.py`
- Modify: `tests/test_gdrive_auth.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gdrive_auth.py`:

```python
def test_sign_in_runs_flow_and_caches_credentials(tmp_path, monkeypatch):
    """sign_in() runs InstalledAppFlow, gets Credentials, writes them to
    token_path, and stores the account email on the instance.

    We stub InstalledAppFlow.from_client_config so the test never opens
    a browser or hits the network.
    """
    fake_creds = MagicMock()
    fake_creds.to_json.return_value = '{"token": "fake-access", "refresh_token": "fake-refresh"}'
    fake_creds.id_token = None   # not present in run_local_server flow
    fake_flow = MagicMock()
    fake_flow.run_local_server.return_value = fake_creds

    # Patch the userinfo HTTP call (sign_in resolves email via OAuth2 v3 userinfo).
    fake_userinfo = MagicMock()
    fake_userinfo.json.return_value = {"email": "tester@example.com"}

    token_file = tmp_path / "gdrive-token.json"
    auth = GDriveAuth(token_path=token_file)

    # NOTE on patch targets: sign_in() does a LAZY import of InstalledAppFlow
    # inside the function (`from google_auth_oauthlib.flow import
    # InstalledAppFlow`) to keep the ~30 MB Google libs out of cold start.
    # A lazy import does NOT create an attribute on gdrive.auth, so
    # `patch("gdrive.auth.InstalledAppFlow.from_client_config")` would fail
    # with AttributeError. Patch the SOURCE module where the name is looked
    # up at import time. `requests`, by contrast, is imported at module top
    # in gdrive/auth.py — so gdrive.auth.requests is a real attribute and
    # patching there works.
    with patch(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_config",
        return_value=fake_flow,
    ), patch("gdrive.auth.requests.get", return_value=fake_userinfo):
        auth.sign_in()

    assert auth.is_signed_in() is True
    assert auth.get_account_email() == "tester@example.com"
    assert token_file.exists(), "Token file should be written to disk"
    on_disk = json.loads(token_file.read_text())
    assert on_disk["token"] == "fake-access"
    assert on_disk["refresh_token"] == "fake-refresh"
    assert on_disk["account_email"] == "tester@example.com"
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_gdrive_auth.py::test_sign_in_runs_flow_and_caches_credentials -v
```

Expected: FAIL with `AttributeError: 'GDriveAuth' object has no attribute 'sign_in'`.

- [ ] **Step 3: Implement `sign_in()` + supporting helpers**

Append to `gdrive/auth.py` (after the `GDriveAuth.__init__`):

```python
    def sign_in(self) -> None:
        """Run the OAuth desktop flow. Opens the user's browser to the
        Google consent screen and blocks until they finish (or cancel).

        Must run in a worker thread — `run_local_server()` blocks. UI
        code is responsible for the threading.
        """
        # Lazy import — Google libs are heavy (~30 MB collectively); only
        # pay the import cost when the user actually clicks Войти.
        from google_auth_oauthlib.flow import InstalledAppFlow

        client_config = {
            "installed": {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
        # port=0 → pick a random free localhost port (RFC 8252 §7.3).
        credentials = flow.run_local_server(port=0, open_browser=True)
        self._credentials = credentials

        # Resolve account email via OAuth2 userinfo (drive.file scope alone
        # doesn't include profile claims, but Google's userinfo endpoint
        # accepts any valid access token).
        self._account_email = self._fetch_account_email(credentials.token)

        self.save_tokens()

    def is_signed_in(self) -> bool:
        """True iff we have credentials in memory (loaded from disk or
        freshly obtained). Does NOT verify the token is still valid on
        the server — refresh logic does that lazily on first API call."""
        return self._credentials is not None

    def get_account_email(self) -> Optional[str]:
        """Email of the signed-in account, or None if not signed in."""
        return self._account_email

    def get_credentials(self):
        """Return the live google.oauth2.credentials.Credentials object
        (or None). Phase 7.1's backup module will call this and build a
        Drive API client from it."""
        return self._credentials

    def save_tokens(self) -> None:
        """Persist credentials + account_email to token_path. Caller is
        responsible for ensuring _credentials is non-None."""
        if self._credentials is None:
            raise RuntimeError("Cannot save tokens before sign_in()")
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.loads(self._credentials.to_json())
        payload["account_email"] = self._account_email
        self.token_path.write_text(json.dumps(payload, indent=2))
        # Defensive: tighten perms on POSIX. No-op on Windows.
        try:
            os.chmod(self.token_path, 0o600)
        except OSError:
            pass   # Windows or filesystem doesn't support — fine, file is in user home

    @staticmethod
    def _fetch_account_email(access_token: str) -> Optional[str]:
        """Call Google's OAuth2 v3 userinfo endpoint to get the email.

        Returns None on any failure — having the email is nice-to-have
        for the Settings status badge, but not having it doesn't break
        sign-in (token is still valid).
        """
        try:
            resp = requests.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json().get("email")
        except requests.RequestException as e:
            logger.warning("Could not fetch GDrive account email: %s", e)
            return None
```

And add the `requests` import near the top of `gdrive/auth.py`, right under the existing imports:

```python
import requests
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_gdrive_auth.py::test_sign_in_runs_flow_and_caches_credentials -v
```

Expected: PASS.

If FAIL with `AttributeError: 'GDriveAuth' object has no attribute 'save_tokens'` — the appended methods didn't land under the class. Re-check indentation: every method must have 4-space indent matching `def __init__`.

- [ ] **Step 5: Commit**

```bash
git add gdrive/auth.py tests/test_gdrive_auth.py
git commit -m "$(cat <<'EOF'
feat(gdrive/auth): sign_in() via InstalledAppFlow + token persistence

sign_in() opens the user's browser to Google's consent screen via
google_auth_oauthlib.flow.InstalledAppFlow.from_client_config +
run_local_server(port=0). Resulting Credentials get written to
~/.audio-transcriber/gdrive-token.json with the account email
(fetched from OAuth2 v3 userinfo).

Google libraries are imported lazily inside sign_in() — ~30 MB cold-
start cost is paid only when the user actually clicks Войти, not at
app startup.

requests is reused (existing dep). Email-fetch failure is non-fatal:
logged + None returned; sign-in still succeeds.

Test mocks both InstalledAppFlow and the userinfo HTTP call so no real
network or browser is touched.
EOF
)"
```

---

### Task A.4: Implement `load_tokens()` + `sign_out()`

**Files:**
- Modify: `gdrive/auth.py`
- Modify: `tests/test_gdrive_auth.py`

- [ ] **Step 1: Write three failing tests**

Append to `tests/test_gdrive_auth.py`:

```python
def test_load_tokens_returns_false_when_file_missing(tmp_path):
    """If the token file doesn't exist, load_tokens() returns False and
    leaves the instance unsigned. Not an error — this is the first-run
    state."""
    auth = GDriveAuth(token_path=tmp_path / "nope.json")
    assert auth.load_tokens() is False
    assert auth.is_signed_in() is False


def test_load_tokens_restores_credentials_and_email(tmp_path):
    """A token file written by save_tokens() must round-trip through
    load_tokens() — credentials become available and account_email is
    populated. Critical for surviving an app restart without re-prompting."""
    token_file = tmp_path / "gdrive-token.json"
    token_file.write_text(json.dumps({
        "token": "fake-access",
        "refresh_token": "fake-refresh",
        "client_id": "fake-client",
        "client_secret": "fake-secret",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/drive.file"],
        "account_email": "rebooted@example.com",
    }))

    auth = GDriveAuth(token_path=token_file)
    assert auth.load_tokens() is True
    assert auth.is_signed_in() is True
    assert auth.get_account_email() == "rebooted@example.com"


def test_sign_out_clears_state_and_removes_file(tmp_path):
    """sign_out() must (a) drop the credentials from memory, (b) drop
    the email, and (c) delete the token file from disk. After sign_out,
    is_signed_in() returns False."""
    token_file = tmp_path / "gdrive-token.json"
    token_file.write_text(json.dumps({
        "token": "x", "refresh_token": "y", "client_id": "a",
        "client_secret": "b", "token_uri": "z",
        "scopes": ["https://www.googleapis.com/auth/drive.file"],
        "account_email": "to-be-removed@example.com",
    }))

    auth = GDriveAuth(token_path=token_file)
    auth.load_tokens()
    assert auth.is_signed_in() is True

    auth.sign_out()
    assert auth.is_signed_in() is False
    assert auth.get_account_email() is None
    assert not token_file.exists(), "Token file should be deleted"


def test_sign_out_when_not_signed_in_is_silent(tmp_path):
    """sign_out() on a fresh instance must not raise — this is the
    'click Выйти after already being signed out' edge case."""
    auth = GDriveAuth(token_path=tmp_path / "nope.json")
    auth.sign_out()   # Must not raise
    assert auth.is_signed_in() is False
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_gdrive_auth.py -v -k "load_tokens or sign_out"
```

Expected: 4 FAILs (`AttributeError: 'GDriveAuth' object has no attribute 'load_tokens'` / `'sign_out'`).

- [ ] **Step 3: Implement `load_tokens()` and `sign_out()`**

Append to the `GDriveAuth` class in `gdrive/auth.py` (after `_fetch_account_email`):

```python
    def load_tokens(self) -> bool:
        """Restore credentials from token_path. Returns True on success,
        False if the file doesn't exist (first-run state). Raises only on
        a malformed token file — that's bug territory, not normal flow.

        Does NOT trigger a refresh. The first API call that needs a fresh
        token will use Credentials.refresh() internally via the Google
        client libraries.
        """
        # Lazy import — same reason as sign_in().
        from google.oauth2.credentials import Credentials

        if not self.token_path.exists():
            return False
        raw = json.loads(self.token_path.read_text())
        # account_email is our addition; google-auth's Credentials doesn't
        # know about it. Pop before handing the rest to Credentials.
        self._account_email = raw.pop("account_email", None)
        self._credentials = Credentials.from_authorized_user_info(raw, SCOPES)
        return True

    def sign_out(self) -> None:
        """Drop credentials from memory and delete the token file.

        Idempotent: calling on a fresh / already-signed-out instance is
        a no-op (no FileNotFoundError, no AttributeError).
        """
        self._credentials = None
        self._account_email = None
        try:
            self.token_path.unlink()
        except FileNotFoundError:
            pass   # Already gone — fine
        except OSError as e:
            logger.warning("Could not delete token file %s: %s", self.token_path, e)
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_gdrive_auth.py -v
```

Expected: 5 tests PASS (1 from A.2 + 1 from A.3 + 3 new + the silent-sign-out edge = 6 total actually; the silent-sign-out case is one of the four new tests written above, so 5 total: path + sign_in + missing_file + roundtrip + clears_state + silent_when_not_signed_in = 6).

- [ ] **Step 5: Commit**

```bash
git add gdrive/auth.py tests/test_gdrive_auth.py
git commit -m "$(cat <<'EOF'
feat(gdrive/auth): load_tokens() + sign_out()

load_tokens() restores Credentials from ~/.audio-transcriber/
gdrive-token.json — returns False if the file doesn't exist (first-
run), True after a successful round-trip. Account email is popped
from our augmented JSON payload before handing the rest to
google.oauth2.credentials.Credentials.from_authorized_user_info.

sign_out() drops in-memory state and deletes the token file.
Idempotent: silent FileNotFoundError handling so 'click Выйти after
already signed out' doesn't blow up the UI.

Three new tests cover: missing-file (False), roundtrip restoration,
sign-out clears state + file. Plus an edge test that sign_out on a
fresh instance is silent.
EOF
)"
```

---

### Task A.5: Token refresh handling — `ensure_valid_credentials()`

**Files:**
- Modify: `gdrive/auth.py`
- Modify: `tests/test_gdrive_auth.py`

The Google API client libraries handle refresh implicitly via the
`Credentials` object's `refresh()` method — but we still need a way for
callers to PROACTIVELY check / refresh before doing batch work (e.g.
before kicking off a backup upload). This task wires that surface.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gdrive_auth.py`:

```python
def test_ensure_valid_credentials_refreshes_expired_token(tmp_path):
    """When the cached access token is expired but the refresh token is
    still valid, ensure_valid_credentials() calls Credentials.refresh()
    and persists the new token to disk."""
    token_file = tmp_path / "gdrive-token.json"
    auth = GDriveAuth(token_path=token_file)

    fake_creds = MagicMock()
    fake_creds.valid = False
    fake_creds.expired = True
    fake_creds.refresh_token = "refresh-still-good"
    fake_creds.to_json.return_value = '{"token": "newly-refreshed"}'
    auth._credentials = fake_creds
    auth._account_email = "user@example.com"

    with patch("gdrive.auth.Request") as mock_request_cls:
        auth.ensure_valid_credentials()

    fake_creds.refresh.assert_called_once()
    # Refresh result should land on disk.
    assert token_file.exists()
    on_disk = json.loads(token_file.read_text())
    assert on_disk["token"] == "newly-refreshed"


def test_ensure_valid_credentials_signs_out_when_refresh_fails(tmp_path):
    """When refresh() raises (revoked token, network down, etc.), the
    UX choice is: drop the bad credentials, force a re-sign-in on next
    use. Better than leaving stale state that fails every subsequent
    API call with a confusing error."""
    from google.auth.exceptions import RefreshError

    token_file = tmp_path / "gdrive-token.json"
    token_file.write_text('{"placeholder": true}')   # so sign_out has something to delete

    auth = GDriveAuth(token_path=token_file)
    fake_creds = MagicMock()
    fake_creds.valid = False
    fake_creds.expired = True
    fake_creds.refresh_token = "revoked-by-user"
    fake_creds.refresh.side_effect = RefreshError("Token has been revoked")
    auth._credentials = fake_creds
    auth._account_email = "revoked@example.com"

    with patch("gdrive.auth.Request"):
        with pytest.raises(RefreshError):
            auth.ensure_valid_credentials()

    # Refresh failure → instance is signed out, token file gone.
    assert auth.is_signed_in() is False
    assert auth.get_account_email() is None
    assert not token_file.exists()


def test_ensure_valid_credentials_noop_when_already_valid(tmp_path):
    """If the credentials are still valid, ensure_valid_credentials() must
    not call refresh() and must not touch the disk."""
    token_file = tmp_path / "gdrive-token.json"
    auth = GDriveAuth(token_path=token_file)

    fake_creds = MagicMock()
    fake_creds.valid = True
    fake_creds.expired = False
    auth._credentials = fake_creds

    auth.ensure_valid_credentials()

    fake_creds.refresh.assert_not_called()
    assert not token_file.exists(), "No save should have happened"
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_gdrive_auth.py -v -k ensure_valid
```

Expected: 3 FAILs (`AttributeError: 'GDriveAuth' object has no attribute 'ensure_valid_credentials'`).

- [ ] **Step 3: Implement `ensure_valid_credentials()`**

Add to the top imports of `gdrive/auth.py` (with the other module-level imports — keep them grouped):

```python
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
```

Append to the `GDriveAuth` class (after `sign_out`):

```python
    def ensure_valid_credentials(self) -> None:
        """Refresh the access token if it's expired. No-op if still valid,
        no-op if not signed in.

        On refresh failure (revoked token, network down, refresh token
        expired): we drop the bad credentials via sign_out() and re-raise
        RefreshError so callers can show the user a "please sign in
        again" message. This avoids the alternative pathology where
        every subsequent API call fails with confusing errors against
        a half-dead Credentials object.
        """
        if self._credentials is None:
            return
        if self._credentials.valid:
            return
        if not self._credentials.expired or not self._credentials.refresh_token:
            return
        try:
            self._credentials.refresh(Request())
        except RefreshError:
            # Auth is gone — drop everything and let the caller re-prompt.
            logger.warning("GDrive token refresh failed; signing out")
            self.sign_out()
            raise
        # Refresh succeeded — persist the new access token to disk so the
        # next process start doesn't have to refresh again.
        self.save_tokens()
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_gdrive_auth.py -v
```

Expected: 8 tests PASS total.

If `test_ensure_valid_credentials_signs_out_when_refresh_fails` errors with `ImportError: cannot import name 'RefreshError'`, your installed `google-auth` version has a different module path — try `from google.auth.exceptions import RefreshError` (current correct path as of `google-auth==2.43.0`) or fall back to `Exception` in the catch and document the version mismatch.

- [ ] **Step 5: Commit**

```bash
git add gdrive/auth.py tests/test_gdrive_auth.py
git commit -m "$(cat <<'EOF'
feat(gdrive/auth): ensure_valid_credentials() with sign-out-on-fail

Proactive refresh path for callers about to do batch work (Phase 7.1
backup uploads will call this before kicking off the upload).

Behaviour matrix:
  - Not signed in        → no-op
  - Credentials valid    → no-op
  - Expired + refresh OK → refresh + persist new token to disk
  - Expired + refresh FAIL → sign_out() + re-raise RefreshError
    (caller's responsibility to surface 'sign in again' to user)

The sign-out-on-failure path is the conservative choice: rather than
leaving the instance in a half-dead state where every subsequent API
call fails with a confusing nested error, we drop the bad creds and
make the user re-authenticate. This UX trade-off is explicit per the
spec's open question on refresh failures (line 218-222 — though that
question was about quota; same principle).

Three new tests: refresh-success path, refresh-failure path
(RefreshError propagates + state cleared), no-op when valid.
EOF
)"
```

---

### Task A.6: PR-A wrap-up

- [ ] **Step 1: Final pytest + lint**

```
pytest -q
python -m ruff check .
```

Expected: 342 baseline + 8 new = 350 green; ruff clean.

If ruff complains about unused imports in `gdrive/auth.py` (the lazy-imported Google libs), confirm those imports live INSIDE function bodies (sign_in, load_tokens), not at module top. Module-top imports must all be used at import time (`requests`, `Request`, `RefreshError`, stdlib).

- [ ] **Step 2: Push and open PR**

```bash
git push -u origin feat/gdrive-phase-7.0-auth
gh pr create --title "feat(gdrive): Phase 7.0 auth module [PR-A]" --body "$(cat <<'EOF'
## Summary

Foundation for Phase 7.0 of the Google Drive backup feature (PR-A of 2).

- New `gdrive/` package with `auth.py` exposing `GDriveAuth`:
  - `sign_in()` — desktop OAuth via `InstalledAppFlow.run_local_server(port=0)`
  - `sign_out()` — idempotent, deletes token file
  - `load_tokens()` — restores credentials across app restart
  - `ensure_valid_credentials()` — proactive refresh, signs out on RefreshError
  - `is_signed_in()` / `get_account_email()` / `get_credentials()` — read-side surface for the UI in PR-B
- Scope is `drive.file` (non-sensitive — app only sees files it created, no Google manual verification needed).
- Token file at `~/.audio-transcriber/gdrive-token.json` — OUTSIDE config.json by design (chicken/egg: config gets backed up to Drive).
- Google libraries imported lazily inside `sign_in()` / `load_tokens()` — ~30 MB cold-start cost paid only when used.
- 3 new pins in requirements.txt (additive, no existing pin touched).

Pure module — no UI integration. PR-B wires the Settings dialog.

See [spec](docs/superpowers/specs/2026-04-30-gdrive-backup-design.md) for the architecture.

## Test plan

- [x] `pytest -q` — 342 baseline + 8 new = 350 green
- [x] `python -m ruff check .` — clean
- [x] Manual smoke deferred to PR-B (no UI to click yet)
- [x] CLIENT_ID / CLIENT_SECRET are placeholder strings — real values land alongside the manual smoke checkbox in PR-B
EOF
)"
```

- [ ] **Step 3: Wait for review + merge before starting PR-B.** Per `feedback_stacked_pr_squash_merge.md`.

---

## PR-B: Settings UI integration

**Branch:** `feat/gdrive-phase-7.0-settings-ui` (from `main` after PR-A merges).

**Goal:** Wire the `GDriveAuth` class into the Settings dialog. Add a "Google Drive" section showing the status badge and Войти/Выйти buttons. Threading: sign-in runs in a worker thread so the UI doesn't freeze while the browser is open.

**Pre-task:**

```bash
git checkout main && git pull --ff-only origin main
git checkout -b feat/gdrive-phase-7.0-settings-ui
```

---

### Task B.1: Add `gdrive_*` config keys to `config.example.json`

**Files:**
- Modify: `config.example.json`

- [ ] **Step 1: Read current config template**

```
cat config.example.json
```

Note the format and trailing-comma convention so the new keys match.

- [ ] **Step 2: Add 4 new keys**

The exact keys per spec line 173-181 (subset relevant to Phase 7.0 — `gdrive_backup_frequency`, `gdrive_backup_audio`, `gdrive_root_folder_id` come in 7.1/7.3/7.4):

```json
"gdrive_enabled": false,
"gdrive_account_email": "",
"gdrive_last_backup": "",
"gdrive_backup_frequency": "off"
```

Insert these into `config.example.json` in alphabetical position. If the file's existing key order is grouped-by-feature instead of alphabetical, insert at the bottom of the cloud / integrations group.

- [ ] **Step 3: Commit**

```bash
git add config.example.json
git commit -m "$(cat <<'EOF'
feat(config): add gdrive_* keys for Phase 7.0

Four new keys covering the Phase 7.0 surface — enabled flag, account
email (for display in Settings), last backup timestamp (for 7.3
overdue check), backup frequency (off by default; daily/weekly land
in 7.3).

gdrive_root_folder_id (cached after first backup) is deliberately
deferred to 7.1 when first backup actually runs. Empty string default
for all string fields rather than null — matches existing config
convention.
EOF
)"
```

---

### Task B.2: Add GDrive Vars + change-callbacks to App

**Files:**
- Modify: `ui/app/__init__.py`

The App owns all StringVars / BooleanVars — Settings dialog binds to them. This is the established pattern (see settings.py line 8-12 docstring + `_parent._openrouter_key_var` reads).

- [ ] **Step 1: Locate the existing Var-init block**

```
grep -n "_openrouter_key_var\|_linear_key_var\|_glide_key_var" ui/app/__init__.py | head
```

Note the line number where the existing per-feature Vars get initialized (typically late in `__init__`, after `_config` is loaded). This is where the new Vars go.

- [ ] **Step 2: Add 4 new Vars + initial state load + 1 instance attribute**

In `ui/app/__init__.py`, in the App class `__init__`, immediately after the last existing `self._<feature>_key_var = ...` line, insert:

```python
        # ── Google Drive (Phase 7.0) ────────────────────────────────
        from gdrive.auth import GDriveAuth
        self._gdrive_auth = GDriveAuth()
        # load_tokens is safe to call even when no token exists yet;
        # returns False and leaves the instance unsigned. App startup
        # cost: one stat() on the token file — negligible.
        self._gdrive_auth.load_tokens()

        self._gdrive_enabled_var = tk.BooleanVar(
            value=bool(self._config.get("gdrive_enabled", False))
        )
        self._gdrive_account_email_var = tk.StringVar(
            value=self._gdrive_auth.get_account_email() or ""
        )
        self._gdrive_status_var = tk.StringVar(
            value=self._compute_gdrive_status_text()
        )
```

- [ ] **Step 3: Add the status-text helper and change callbacks**

Find where the other `_compute_*` / `_on_*_changed` methods live on App (typically grouped near the end of the class — search `_on_linear_enabled_changed`). Insert the following methods alongside them:

```python
    # ── Google Drive (Phase 7.0) ────────────────────────────────────

    def _compute_gdrive_status_text(self) -> str:
        """Status badge text shown in the Settings dialog.

        Three states:
          - Signed in + email known   → "✓ Подключён к user@example.com"
          - Signed in + email unknown → "✓ Подключён"
          - Not signed in             → "Не подключён"
        """
        if not self._gdrive_auth.is_signed_in():
            return "Не подключён"
        email = self._gdrive_auth.get_account_email()
        if email:
            return f"✓ Подключён к {email}"
        return "✓ Подключён"

    def _on_gdrive_signed_in(self) -> None:
        """Called from the sign-in worker thread (via Tk after) on success.
        Updates Vars + persists email + enabled flag to config.json."""
        email = self._gdrive_auth.get_account_email() or ""
        self._gdrive_account_email_var.set(email)
        self._gdrive_status_var.set(self._compute_gdrive_status_text())
        self._gdrive_enabled_var.set(True)
        self._config["gdrive_account_email"] = email
        self._config["gdrive_enabled"] = True
        save_config(self._config)

    def _on_gdrive_signed_out(self) -> None:
        """Called from the Выйти button handler. Mirrors _on_gdrive_signed_in
        but in reverse: empty email, disable flag, persist."""
        self._gdrive_auth.sign_out()
        self._gdrive_account_email_var.set("")
        self._gdrive_status_var.set(self._compute_gdrive_status_text())
        self._gdrive_enabled_var.set(False)
        self._config["gdrive_account_email"] = ""
        self._config["gdrive_enabled"] = False
        save_config(self._config)
```

`tk` and `save_config` should already be imported at the top of `ui/app/__init__.py` (used by other features). If not, add:

```python
import tkinter as tk
from utils import save_config
```

- [ ] **Step 4: Commit**

```bash
git add ui/app/__init__.py
git commit -m "$(cat <<'EOF'
feat(ui/app): GDriveAuth + state vars for Phase 7.0

App now owns a GDriveAuth instance, calls load_tokens() on startup
(safe no-op if no cached token), and exposes three Vars for the
Settings dialog to bind: enabled flag, account email, status text.

_compute_gdrive_status_text() produces the Russian status badge with
three states (Не подключён / ✓ Подключён / ✓ Подключён к X). Helpers
_on_gdrive_signed_in / _on_gdrive_signed_out persist state to
config.json and refresh the Vars; both are called from the Settings
dialog's button handlers in the next task.
EOF
)"
```

---

### Task B.3: Build the GDrive section in `settings.py`

**Files:**
- Modify: `ui/dialogs/settings.py`

- [ ] **Step 1: Register the new section in the dialog body**

In `ui/dialogs/settings.py`, locate the `__init__` block at lines 96-104:

```python
        self._build_appearance_section(body)
        self._build_transcription_section(body)
        self._build_diarization_section(body)
        self._build_audio_section(body)
        self._build_cloud_section(body)
        self._build_dictionaries_section(body)
        self._build_openrouter_section(body)
        self._build_linear_section(body)
        self._build_glide_section(body)
```

Append one line:

```python
        self._build_gdrive_section(body)
```

- [ ] **Step 2: Add the `_build_gdrive_section` method**

Add this method to `SettingsDialog`, immediately after `_build_glide_section` (search for that method name to find the insertion point):

```python
    # ── Google Drive section (Phase 7.0) ──────────────────────────────

    def _build_gdrive_section(self, parent) -> None:
        """Google Drive backup: sign-in/out + status badge.

        Phase 7.0 surface only — no backup-now button (7.1), no
        frequency dropdown (7.3), no audio opt-in (7.4). Adding those
        widgets later just extends this method.

        Threading: sign_in() blocks while the browser is open; we run
        it in a daemon thread and route the result back to the Tk loop
        via `self.after(0, ...)` so widget updates happen on the main
        thread. Mirrors the _validate_openrouter pattern.
        """
        section = self._section_card(parent, "Google Drive", row=9)

        # Status row — badge bound to the App's _gdrive_status_var.
        label(section, "Статус").grid(
            row=0, column=0, padx=(4, 8), pady=6, sticky="w",
        )
        self._gdrive_status_label = ctk.CTkLabel(
            section,
            textvariable=self._parent._gdrive_status_var,
            anchor="w",
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=12),
        )
        self._gdrive_status_label.grid(
            row=0, column=1, columnspan=2, padx=4, pady=6, sticky="ew",
        )

        # Action row — Войти + Выйти (one of them disabled at any time).
        self._gdrive_signin_btn = primary_button(
            section, text="Войти через Google",
            command=self._handle_gdrive_signin, width=180,
        )
        self._gdrive_signin_btn.grid(row=1, column=0, columnspan=2, padx=4, pady=6, sticky="w")

        self._gdrive_signout_btn = tonal_button(
            section, text="Выйти",
            command=self._handle_gdrive_signout, width=100,
        )
        self._gdrive_signout_btn.grid(row=1, column=2, padx=(4, 4), pady=6, sticky="e")

        # Initial button enabled-state reflects current sign-in state.
        self._refresh_gdrive_button_state()

    def _refresh_gdrive_button_state(self) -> None:
        """Войти is enabled iff not signed in; Выйти iff signed in. Called
        after every state change so the UI matches the GDriveAuth state."""
        if self._parent._gdrive_auth.is_signed_in():
            self._gdrive_signin_btn.configure(state="disabled")
            self._gdrive_signout_btn.configure(state="normal")
        else:
            self._gdrive_signin_btn.configure(state="normal")
            self._gdrive_signout_btn.configure(state="disabled")

    def _handle_gdrive_signin(self) -> None:
        """Войти clicked — spawn a worker that runs sign_in() (blocks on
        browser). Disable the button immediately so double-click can't
        spawn two flows."""
        self._gdrive_signin_btn.configure(state="disabled", text="Открываю браузер...")

        def worker():
            try:
                self._parent._gdrive_auth.sign_in()
                self.after(0, self._on_gdrive_signin_success)
            except Exception as e:                           # noqa: BLE001 — surface any OAuth failure
                _logger.exception("GDrive sign-in failed: %s", e)
                self.after(0, lambda: self._on_gdrive_signin_failure(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_gdrive_signin_success(self) -> None:
        """Worker → main thread: refresh state + restore button text."""
        self._parent._on_gdrive_signed_in()
        self._gdrive_signin_btn.configure(text="Войти через Google")
        self._refresh_gdrive_button_state()

    def _on_gdrive_signin_failure(self, error_msg: str) -> None:
        """Worker → main thread: restore button + show error in status."""
        self._gdrive_signin_btn.configure(text="Войти через Google")
        self._parent._gdrive_status_var.set(f"⚠ Ошибка входа: {error_msg[:80]}")
        self._refresh_gdrive_button_state()

    def _handle_gdrive_signout(self) -> None:
        """Выйти clicked — sync; sign_out() is fast (file delete)."""
        self._parent._on_gdrive_signed_out()
        self._refresh_gdrive_button_state()
```

The `noqa: BLE001` comment is intentional and per CLAUDE.md convention: when narrowing an except is impossible (here: any OAuth failure mode — network, user cancel, GCP misconfig, all distinct exception types) we keep `Exception` but document why. The line `_logger.exception(...)` ensures the full stack ends up in the log file for debugging.

- [ ] **Step 3: Sanity-check Settings dialog still loads**

This is a UI sanity check, not a test:

```
python -c "import customtkinter as ctk; from ui.dialogs.settings import SettingsDialog; print('imports ok')"
```

Expected: `imports ok`. If `ImportError: cannot import GDriveAuth` — `_parent._gdrive_auth` reference is wrong; verify B.2 Step 2 set the attribute on App before the dialog can be opened.

- [ ] **Step 4: Commit**

```bash
git add ui/dialogs/settings.py
git commit -m "$(cat <<'EOF'
feat(ui/settings): Google Drive section (sign-in/out + status badge)

New _build_gdrive_section (row=9, after Glide) renders the Phase 7.0
surface: bound status label, Войти button, Выйти button. Войти runs
GDriveAuth.sign_in() in a daemon thread (browser blocks) and routes
result back to the Tk loop via self.after(0, ...) — same pattern as
_validate_openrouter.

Button states are mutually exclusive: Войти enabled iff not signed
in, Выйти iff signed in. _refresh_gdrive_button_state() rebinds
after every state transition.

Failure path: any OAuth exception (network, user cancel, GCP misconfig)
is logged via _logger.exception and surfaced as a truncated ⚠ status
message — no popup, no app crash, button returns to enabled.
EOF
)"
```

---

### Task B.4: Smoke test for the new Settings section

**Files:**
- Create: `tests/test_settings_gdrive.py`

Most existing UI tests are constants-checks (`test_ui_constants.py`) rather than full widget renders — customtkinter needs a Tk root which CI doesn't have. This task adds the equivalent smoke: import the dialog module, verify the new method exists, verify the App-class additions don't break instantiation.

- [ ] **Step 1: Write the smoke tests**

Create `tests/test_settings_gdrive.py`:

```python
"""Phase 7.0 smoke tests for the Settings dialog GDrive section.

Headless — no Tk root spun up. Verifies imports + class surface so
ImportError or AttributeError regressions surface in CI without
needing a display.
"""
from __future__ import annotations


def test_settings_dialog_has_gdrive_section_builder():
    """SettingsDialog must expose _build_gdrive_section as a method."""
    from ui.dialogs.settings import SettingsDialog
    assert hasattr(SettingsDialog, "_build_gdrive_section")
    assert callable(SettingsDialog._build_gdrive_section)


def test_settings_dialog_has_gdrive_handlers():
    """Sign-in/out handlers must exist on SettingsDialog (referenced by
    button commands in _build_gdrive_section)."""
    from ui.dialogs.settings import SettingsDialog
    for method in (
        "_handle_gdrive_signin",
        "_handle_gdrive_signout",
        "_on_gdrive_signin_success",
        "_on_gdrive_signin_failure",
        "_refresh_gdrive_button_state",
    ):
        assert hasattr(SettingsDialog, method), f"Missing {method}"


def test_app_class_has_gdrive_attributes():
    """App must define the GDrive Var attributes + change callbacks
    that the Settings dialog binds to. We inspect the class, not an
    instance — avoids needing a real Tk root."""
    import ui.app
    # Source-level check: __init__ body and method definitions both
    # contain the right strings. This is a weak test but it's what's
    # safely runnable without Tk.
    import inspect
    src = inspect.getsource(ui.app)
    for marker in (
        "_gdrive_auth",
        "_gdrive_status_var",
        "_gdrive_account_email_var",
        "_compute_gdrive_status_text",
        "_on_gdrive_signed_in",
        "_on_gdrive_signed_out",
    ):
        assert marker in src, f"App source missing {marker}"
```

- [ ] **Step 2: Run the new tests**

```
pytest tests/test_settings_gdrive.py -v
```

Expected: 3 PASS.

- [ ] **Step 3: Run the full suite**

```
pytest -q
python -m ruff check .
```

Expected: 342 baseline + 8 from PR-A + 3 new = 353 green; ruff clean.

- [ ] **Step 4: Commit**

```bash
git add tests/test_settings_gdrive.py
git commit -m "$(cat <<'EOF'
test(settings/gdrive): headless smoke tests for Phase 7.0 UI

Three smoke tests verify the class surface added in B.2 and B.3:
- SettingsDialog._build_gdrive_section exists and is callable
- All five button handlers exist on SettingsDialog
- App source contains the expected GDrive var/callback names

Headless source-inspection — no Tk root needed (CI doesn't have a
display). Same pattern as tests/test_ui_constants.py.
EOF
)"
```

---

### Task B.5: Manual smoke test + paste real OAuth creds

**Files:**
- Modify (locally, do NOT commit final secrets to a public repo): `gdrive/auth.py` lines defining `CLIENT_ID` / `CLIENT_SECRET`

This task gates PR-B merge on a successful real OAuth round-trip with the GCP credentials from Pre-flight.

- [ ] **Step 1: Paste real OAuth client values**

Open `gdrive/auth.py`. Replace the two placeholder constants:

```python
CLIENT_ID = "<paste from Pre-flight>.apps.googleusercontent.com"
CLIENT_SECRET = "<paste from Pre-flight>"
```

Per RFC 8252 these are public for installed apps and intentionally shipped in the binary; the GCP project is `audio-transcriber-personal`.

- [ ] **Step 2: Launch the app from the repo root**

```bash
python app.py
```

(Per memory `feedback_run_app_from_main_not_worktree.md`: run from the absolute path of the main checkout, not from a Conductor worktree, so config.json + history/ are real.)

- [ ] **Step 3: Walk through sign-in**

1. Open Настройки → scroll to the new "Google Drive" section
2. Status should read "Не подключён"
3. Click "Войти через Google"
4. Button text changes to "Открываю браузер..."
5. Default browser opens → Google consent screen
6. Sign in with a Google account, click Continue, then Allow
7. Browser redirects to `localhost:<port>/` showing "The authentication flow has completed."
8. Back in the app: status updates to "✓ Подключён к <your email>"
9. Войти button is disabled; Выйти is enabled
10. Verify on disk: `~/.audio-transcriber/gdrive-token.json` exists, contains `{"token": ..., "refresh_token": ..., "account_email": ...}`

- [ ] **Step 4: Walk through restart-persistence**

1. Close the app
2. Reopen — Settings → Google Drive section → status STILL shows "✓ Подключён к <email>"
3. Confirms `load_tokens()` runs at startup and rehydrates state

- [ ] **Step 5: Walk through sign-out**

1. Click Выйти
2. Status reverts to "Не подключён"
3. Войти re-enabled, Выйти disabled
4. Verify on disk: `~/.audio-transcriber/gdrive-token.json` no longer exists

- [ ] **Step 6: Walk through Войти-then-cancel**

1. Click Войти through Google
2. When browser opens, close the tab WITHOUT consenting
3. App should: re-enable Войти button + status reads "⚠ Ошибка входа: ..." (truncated message)
4. Log file `logs/app.log` should have the full traceback

- [ ] **Step 7: Decide whether to commit the real CLIENT_ID/SECRET**

Per the spec line 74-76: "Public client_id ships in `gdrive/auth.py` as a constant. Anyone with the binary uses the same OAuth project; that's expected for desktop apps."

If proceeding with the public-installed-app model (recommended; matches Discord/Notion convention), commit the real values:

```bash
git add gdrive/auth.py
git commit -m "$(cat <<'EOF'
feat(gdrive/auth): wire real GCP OAuth client credentials

CLIENT_ID + CLIENT_SECRET point at the audio-transcriber-personal GCP
project. Per RFC 8252 these are public values for installed apps —
shipping them in the binary is the documented OAuth pattern, not a
secret leak. Security is enforced by:
  - drive.file scope (app only sees files it creates)
  - per-user consent screen (Google shows the user the app + scope)
  - per-account token (each user gets their own refresh token)

Manual smoke (all on Windows 11 + GTX 1650 Ti laptop):
  - sign-in: browser opens, consent, status → ✓ Подключён к <email>
  - restart persistence: token file rehydrates, status stays signed-in
  - sign-out: file removed, button states swap
  - cancel mid-flow: ⚠ error in status, button re-enabled, log has trace
EOF
)"
```

If choosing instead to keep them out of git (each contributor brings their own):

```bash
# Don't add gdrive/auth.py to staging; create a .env-style override
# pattern. Out of scope for Phase 7.0; document the decision in CLAUDE.md
# under "Don't" with the rationale.
```

For Phase 7.0, **default choice is to commit** — it matches the spec and the installed-app convention. Revisit only if a real privacy threat appears.

---

### Task B.6: PR-B wrap-up

- [ ] **Step 1: Final pytest + lint**

```
pytest -q
python -m ruff check .
```

Expected: 353 green, ruff clean.

- [ ] **Step 2: Push and open PR**

```bash
git push -u origin feat/gdrive-phase-7.0-settings-ui
gh pr create --title "feat(gdrive): Phase 7.0 Settings UI integration [PR-B]" --body "$(cat <<'EOF'
## Summary

Phase 7.0 closeout: Settings dialog now has a working "Google Drive" section. Users can sign in via Google OAuth (desktop loopback flow), see their connected account, and sign out. No backup functionality yet — that's Phase 7.1.

Built on PR-A's `gdrive.auth.GDriveAuth` class. Phase 7.0 is independently shippable per the spec: after this PR merges, the Войти / Выйти flow is end-to-end functional even though nothing is uploaded to Drive yet.

### Changes

| File | What |
|---|---|
| `config.example.json` | +4 `gdrive_*` keys (enabled, account_email, last_backup, frequency) |
| `ui/app/__init__.py` | `GDriveAuth` instance, 3 Vars, status-text helper, sign-in/out callbacks |
| `ui/dialogs/settings.py` | New `_build_gdrive_section` (row=9) — status label + Войти + Выйти buttons; threading via `threading.Thread` + `self.after(0, ...)` |
| `tests/test_settings_gdrive.py` | 3 headless smoke tests verifying class surface |
| `gdrive/auth.py` | (optional commit) Real CLIENT_ID/CLIENT_SECRET from the GCP project |

### Test plan

- [x] `pytest -q` — 353 tests green (342 baseline + 8 PR-A + 3 PR-B)
- [x] `python -m ruff check .` — clean
- [x] Manual smoke (per Task B.5):
  - [x] Sign-in → browser opens → consent → status updates
  - [x] Restart → state persists from `~/.audio-transcriber/gdrive-token.json`
  - [x] Sign-out → file removed, status reverts
  - [x] Войти-then-cancel → ⚠ error in status, button re-enabled

## Closes

Phase 7.0 of the Google Drive backup feature per [spec](docs/superpowers/specs/2026-04-30-gdrive-backup-design.md).
EOF
)"
```

- [ ] **Step 3: Wait for review + merge.**

---

## Post-merge: CLAUDE.md update

After PR-B merges, open a tiny docs PR:

```bash
git checkout main && git pull --ff-only origin main
git checkout -b docs/claude-md-after-gdrive-7.0
```

Edit `CLAUDE.md`:

1. In the "Active work / context" section, replace the Phase 7 stub bullet:

```markdown
- **Phase 7.0** (May 2026, shipped): Google Drive auth + Settings UI.
  New `gdrive/` package with `auth.py::GDriveAuth` wrapping
  `google_auth_oauthlib.flow.InstalledAppFlow` for the desktop OAuth
  loopback dance; tokens cached at `~/.audio-transcriber/gdrive-token.json`
  (outside `config.json` because the latter gets backed up to Drive).
  Settings dialog gained a "Google Drive" section (row=9) with status
  badge + Войти/Выйти buttons; sign-in runs in a daemon thread to keep
  the UI responsive while the browser blocks. Scope is `drive.file`
  (non-sensitive — no Google manual verification needed). Phase 7.1+
  (backup, restore, scheduler, audio opt-in) remain unstarted; spec at
  `docs/superpowers/specs/2026-04-30-gdrive-backup-design.md`.
```

2. In the "Where things live" table, add one row:

```markdown
| Google Drive auth | `gdrive/auth.py` |
```

3. Bump the test baseline in the test contract section: `pytest # 342 → 353`.

Commit + push + PR:

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md after Phase 7.0 ships"
git push -u origin docs/claude-md-after-gdrive-7.0
gh pr create --title "docs: update CLAUDE.md after Phase 7.0 ships" --body "Records Phase 7.0 closure (GDrive auth + Settings UI). Phase 7.1 (manual backup) is the next slice."
```

---

## Plan self-review

**Spec coverage** — every Phase 7.0 spec section has a corresponding task:

- Auth (spec §Architecture > Auth, lines 56-77): Tasks A.2 (path), A.3 (sign_in), A.4 (load/sign_out), A.5 (refresh)
- Scope `drive.file` (line 60): A.3 Step 3 SCOPES constant
- Token storage at `~/.audio-transcriber/gdrive-token.json` outside config.json (lines 68-70, 184-186): A.2 Step 4 `_default_token_path`
- Public client_id in `gdrive/auth.py` (lines 74-76): A.2 Step 4 placeholder + B.5 Step 1 real values
- Settings UI section (spec §Architecture > New UI, lines 51-54): Tasks B.1-B.4
- Config keys (spec §Configuration additions, lines 173-181): B.1 (Phase 7.0 subset — `gdrive_enabled`, `gdrive_account_email`, `gdrive_last_backup`, `gdrive_backup_frequency`); audio + frequency-dropdown UI deferred to 7.3/7.4 by spec design
- Tests `tests/test_gdrive_auth.py` covering token persistence, refresh, expired-token (spec §Testing line 194): A.2-A.5

**Spec scope NOT in this plan** — explicitly deferred per phasing:

- `gdrive/client.py`, `backup.py`, `restore.py`, `scheduler.py`, `sync.py` — Phases 7.1-7.5
- `ui/dialogs/restore.py` — Phase 7.2
- Backup payload manifest schema — Phase 7.1
- Auto-schedule — Phase 7.3
- Audio opt-in — Phase 7.4
- Encryption-at-rest open question — out of scope per spec recommendation

**Placeholder scan** — no TBD / TODO / "implement later" in code steps. The only conditional is Task B.5 Step 7 ("commit the real client_id or keep it local") which is a documented two-branch decision with both paths fully specified.

**Type consistency** —
- `GDriveAuth` constructor signature `(token_path: Optional[Path] = None)` used consistently in A.2, A.3, A.4, A.5 tests
- `is_signed_in() -> bool`, `get_account_email() -> Optional[str]` — same return types in tests (A.2, A.3, A.4), Settings dialog binders (B.3), and CLAUDE.md doc
- `_gdrive_status_var` (BooleanVar? StringVar?): defined as `tk.StringVar` in B.2 Step 2, bound via `textvariable=` in B.3 Step 2 (correct for StringVar)
- `_gdrive_enabled_var` is `BooleanVar`; only used by `_on_gdrive_signed_in`/`_signed_out` setters in B.2 Step 3 — no UI checkbox bound to it in Phase 7.0 (the checkbox lands in 7.3's frequency dropdown migration). Defensive: present so 7.1's backup-now button can read it.
- `_compute_gdrive_status_text()` in B.2 returns the same three strings the B.3 status label binds to — consistent.

---

## Glossary

- **drive.file scope** — Google OAuth scope `https://www.googleapis.com/auth/drive.file`. Non-sensitive: app only sees files it created. No manual app verification needed (unlike full `drive` scope).
- **Installed-app OAuth** — RFC 8252 desktop OAuth flow. App starts a local HTTP server, browser redirects to it, app catches the auth code. `client_secret` is public for this flow (apps can't keep secrets).
- **`GDriveAuth`** — The `gdrive.auth.GDriveAuth` class. Owns the OAuth state and the token file on disk. One instance per App.
- **Token file** — `~/.audio-transcriber/gdrive-token.json`. Holds the access + refresh token + account email. Lives outside `config.json` because config gets backed up to Drive (chicken/egg).
- **`ensure_valid_credentials()`** — Proactive refresh path callers use before batch work (7.1's backup uploads). Refreshes if expired, signs out + re-raises on RefreshError.
- **PR-A** — Foundation: the `gdrive/auth.py` module + unit tests. Nothing imports it on this branch.
- **PR-B** — Integration: Settings dialog wires the class into the UI. End of PR-B = Phase 7.0 shipped.
- **Phase 7.0** — The slice covered by this plan: auth + UI section. No backup functionality yet (that's 7.1).
