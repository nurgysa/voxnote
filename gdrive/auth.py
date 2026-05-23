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

import requests
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request

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

    def __init__(self, token_path: Path | None = None) -> None:
        self.token_path = token_path or _default_token_path()
        self._credentials = None       # populated by load_tokens / sign_in
        self._account_email = None     # populated by sign_in (from id_token)

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

    def get_account_email(self) -> str | None:
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
    def _fetch_account_email(access_token: str) -> str | None:
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
        except (requests.RequestException, ValueError) as e:
            # ValueError catches json.JSONDecodeError (non-JSON 200 from a
            # captive portal / corporate MITM proxy / Google outage page)
            # AND UnicodeDecodeError (response body in an encoding requests
            # can't decode). Both are realistic in the wild and would
            # otherwise bubble up AFTER OAuth succeeded but BEFORE
            # save_tokens() runs — blocking the user from signing in
            # despite holding valid credentials. Per the docstring this
            # function is best-effort: any failure → None, no exception.
            logger.warning("Could not fetch GDrive account email: %s", e)
            return None

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
