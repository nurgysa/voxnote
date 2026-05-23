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
