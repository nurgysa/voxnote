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
