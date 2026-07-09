"""Tests for Hermes webhook emission from CLI subcommands.

Verifies three spec §9.4 requirements:
  (a) disabled config → requests.post never called during _cmd_transcribe
  (b) enabled config (via env) → post called once, stdout JSON contract unchanged
  (c) requests.RequestException during emit → exit code still EXIT_OK

Mocking strategy:
- core.run_transcribe is replaced via monkeypatch.setattr to avoid any real
  audio/network path.
- integrations.hermes.client.requests.post is patched to capture calls.
- Env vars activate/deactivate via monkeypatch.setenv.
"""
from __future__ import annotations

import io
import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from cli.app import EXIT_OK, _cmd_transcribe

# ── Shared fixture: a fake TranscribeResult ───────────────────────────

class _FakeResult:
    text = "Тестовая транскрипция"
    language = "ru"
    provider = "AssemblyAI"
    segments = []
    diarized = False

    def to_dict(self):
        return {
            "text": self.text,
            "language": self.language,
            "provider": self.provider,
            "segments": self.segments,
            "diarized": self.diarized,
        }


def _make_args(*, json_flag=True, save=False, quiet=True, api_key="test-key"):
    """Build a minimal args namespace for _cmd_transcribe."""
    args = types.SimpleNamespace()
    args.audio = "test.m4a"
    args.provider = "AssemblyAI"
    args.api_key = api_key
    args.language = "ru"
    args.diarize = False
    args.hotwords = None
    args.denoise = False
    args.save = save
    args.json = json_flag
    args.quiet = quiet
    return args


# ── CLI provider-specific env key resolution ──────────────────────────

def test_cmd_transcribe_uses_provider_specific_env_key(monkeypatch, capsys):
    monkeypatch.setenv("VOXNOTE_API_KEY", "legacy-key")
    monkeypatch.setenv("VOXNOTE_ASSEMBLYAI_API_KEY", "assemblyai-key")
    monkeypatch.delenv("VOXNOTE_HERMES_WEBHOOK_ENABLED", raising=False)

    captured = {}

    def _fake_run_transcribe(audio, **kwargs):
        captured["audio"] = audio
        captured["kwargs"] = kwargs
        return _FakeResult()

    with patch("cli.core.run_transcribe", side_effect=_fake_run_transcribe), \
         patch("cli.config.base_config", return_value={}):
        code = _cmd_transcribe(_make_args(api_key=None))

    assert code == EXIT_OK
    assert captured["kwargs"]["provider"] == "AssemblyAI"
    assert captured["kwargs"]["api_key"] == "assemblyai-key"


# ── (a) Disabled config → post never called ───────────────────────────

def test_cmd_transcribe_disabled_no_post(monkeypatch, capsys):
    """When Hermes is disabled (default), requests.post is never called."""
    # Ensure the env flag is NOT set
    monkeypatch.delenv("VOXNOTE_HERMES_WEBHOOK_ENABLED", raising=False)

    with patch("cli.core.run_transcribe", return_value=_FakeResult()), \
         patch("cli.config.base_config", return_value={}), \
         patch("integrations.hermes.client.requests.post") as mock_post:
        code = _cmd_transcribe(_make_args())

    assert code == EXIT_OK
    mock_post.assert_not_called()


# ── (b) Enabled → post called once, stdout JSON unchanged ────────────

def test_cmd_transcribe_enabled_post_called_once(monkeypatch, capsys):
    """When Hermes is enabled via env, post is called exactly once."""
    monkeypatch.setenv("VOXNOTE_HERMES_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv(
        "VOXNOTE_HERMES_WEBHOOK_URL",
        "http://localhost:8644/webhooks/audio-transcribed",
    )
    monkeypatch.setenv("VOXNOTE_HERMES_WEBHOOK_SECRET", "test-secret")

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200

    with patch("cli.core.run_transcribe", return_value=_FakeResult()), \
         patch("cli.config.base_config", return_value={}), \
         patch("integrations.hermes.client.requests.post", return_value=mock_resp) as mock_post:
        code = _cmd_transcribe(_make_args(json_flag=True))

    assert code == EXIT_OK
    mock_post.assert_called_once()

    # stdout must still be valid JSON with unchanged keys
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert "text" in payload
    assert payload["text"] == _FakeResult.text


def test_cmd_transcribe_enabled_stdout_json_unchanged(monkeypatch, capsys):
    """--json output is byte-for-byte the TranscribeResult dict, no Hermes noise."""
    monkeypatch.setenv("VOXNOTE_HERMES_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("VOXNOTE_HERMES_WEBHOOK_SECRET", "s")
    monkeypatch.setenv(
        "VOXNOTE_HERMES_WEBHOOK_URL",
        "http://localhost:8644/webhooks/audio-transcribed",
    )

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200

    with patch("cli.core.run_transcribe", return_value=_FakeResult()), \
         patch("cli.config.base_config", return_value={}), \
         patch("integrations.hermes.client.requests.post", return_value=mock_resp):
        code = _cmd_transcribe(_make_args(json_flag=True))

    assert code == EXIT_OK
    captured = capsys.readouterr()
    # stdout must be a single JSON object (exactly one non-empty line)
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert len(lines) == 1, f"Expected 1 stdout line, got: {lines}"
    parsed = json.loads(lines[0])
    assert set(parsed.keys()) == {"text", "language", "provider", "segments", "diarized"}


# ── (c) RequestException → exit code still EXIT_OK ───────────────────

def test_cmd_transcribe_request_exception_exit_ok(monkeypatch, capsys):
    """A requests.RequestException during webhook emit must not change exit code."""
    import requests as _requests

    monkeypatch.setenv("VOXNOTE_HERMES_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("VOXNOTE_HERMES_WEBHOOK_SECRET", "s")
    monkeypatch.setenv(
        "VOXNOTE_HERMES_WEBHOOK_URL",
        "http://localhost:8644/webhooks/audio-transcribed",
    )

    with patch("cli.core.run_transcribe", return_value=_FakeResult()), \
         patch("cli.config.base_config", return_value={}), \
         patch(
             "integrations.hermes.client.requests.post",
             side_effect=_requests.RequestException("connection refused"),
         ):
        code = _cmd_transcribe(_make_args(json_flag=True))

    assert code == EXIT_OK
    # The transcript must still appear on stdout
    captured = capsys.readouterr()
    assert captured.out.strip()  # non-empty
