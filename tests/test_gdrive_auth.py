"""Tests for gdrive.auth.GDriveAuth — Phase 7.0.

Pure module — no real OAuth, no real network. Stubs:
  - InstalledAppFlow via patching gdrive.auth.InstalledAppFlow
  - Credentials roundtrip via tmp_path
  - Token refresh via MagicMock on Credentials

Pattern mirrors tests/test_tasks_openrouter_client.py (mock the network
boundary, exercise the logic).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gdrive.auth import TOKEN_FILENAME, GDriveAuth


def test_token_path_under_user_home(tmp_path, monkeypatch):
    """Token file lives at ~/.voxnote/gdrive-token.json
    by default. We use tmp_path as a fake home for isolation."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path))   # Windows
    monkeypatch.setenv("HOME", str(tmp_path))          # POSIX

    auth = GDriveAuth()
    assert auth.token_path == tmp_path / ".voxnote" / TOKEN_FILENAME


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


def test_sign_in_still_succeeds_when_userinfo_returns_non_json(tmp_path):
    """Regression for Codex P2 on PR #40: when Google's userinfo endpoint
    returns a non-JSON 200 (captive portal, corporate MITM, transient
    Google outage page), resp.json() raises json.JSONDecodeError — a
    ValueError, NOT a requests.RequestException. The original except clause
    only caught RequestException, so the JSON error would bubble up AFTER
    OAuth succeeded but BEFORE save_tokens() ran — leaving the user with
    valid credentials in memory that never reached disk.

    _fetch_account_email's contract is best-effort: email is a nice-to-have
    for the status badge, but its absence must NEVER block sign-in. This
    test pins that contract.
    """
    fake_creds = MagicMock()
    fake_creds.to_json.return_value = '{"token": "fake-access", "refresh_token": "fake-refresh"}'
    fake_flow = MagicMock()
    fake_flow.run_local_server.return_value = fake_creds

    # Mock a response where raise_for_status() passes (HTTP 200) but
    # json() raises a JSONDecodeError — simulates Google returning an
    # HTML error page with 200 status (rare but observed in the wild).
    fake_userinfo = MagicMock()
    fake_userinfo.raise_for_status.return_value = None  # 200 OK
    fake_userinfo.json.side_effect = json.JSONDecodeError("Expecting value", "<html>...", 0)

    token_file = tmp_path / "gdrive-token.json"
    auth = GDriveAuth(token_path=token_file)

    with patch(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_config",
        return_value=fake_flow,
    ), patch("gdrive.auth.requests.get", return_value=fake_userinfo):
        # Must NOT raise — the original buggy code did raise here.
        auth.sign_in()

    # Sign-in completed successfully despite the email lookup failing.
    assert auth.is_signed_in() is True
    assert auth.get_account_email() is None, "Email lookup failed → None"
    assert token_file.exists(), "Token MUST still be persisted to disk"
    on_disk = json.loads(token_file.read_text())
    assert on_disk["token"] == "fake-access"
    assert on_disk["account_email"] is None


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

    with patch("gdrive.auth.Request") as mock_request_cls:  # noqa: F841
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
