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

from gdrive.auth import GDriveAuth, TOKEN_FILENAME


def test_token_path_under_user_home(tmp_path, monkeypatch):
    """Token file lives at ~/.audio-transcriber/gdrive-token.json
    by default. We use tmp_path as a fake home for isolation."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path))   # Windows
    monkeypatch.setenv("HOME", str(tmp_path))          # POSIX

    auth = GDriveAuth()
    assert auth.token_path == tmp_path / ".audio-transcriber" / TOKEN_FILENAME


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
