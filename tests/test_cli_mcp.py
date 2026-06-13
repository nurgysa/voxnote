"""cli.mcp_server — tool registration, headless import, core delegation.

Skipped entirely when the optional `mcp` dep is absent (requirements-mcp.txt),
so the core suite stays green without it.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

import cli.mcp_server as srv  # noqa: E402  (after importorskip)
from cli import config, core  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_mcp_server_import_is_headless():
    # Same headless guarantee as the CLI: importing the server must not pull the
    # GUI / PortAudio. Clean subprocess so other tests can't pollute sys.modules.
    probe = (
        "import sys\n"
        "import cli.mcp_server\n"
        "bad = [m for m in ('customtkinter', 'sounddevice', 'ui.app') "
        "if m in sys.modules]\n"
        "print(','.join(bad))\n"
        "sys.exit(1 if bad else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"cli.mcp_server pulled forbidden modules: {result.stdout.strip()!r}\n"
        f"{result.stderr}"
    )


def test_all_pipeline_tools_registered():
    names = sorted(tool.name for tool in asyncio.run(srv.mcp.list_tools()))
    assert names == [
        "extract_tasks",
        "generate_protocol",
        "list_containers",
        "send_tasks",
        "transcribe_audio",
    ]


def test_transcribe_tool_resolves_secrets_and_delegates(monkeypatch):
    # No config.json values; key from env. The tool must resolve provider/key
    # server-side and forward to core.run_transcribe (on_status must be None so
    # nothing leaks onto the JSON-RPC stdout channel).
    monkeypatch.setattr(config, "base_config", dict)
    monkeypatch.setenv("VOXNOTE_API_KEY", "test-key")

    captured = {}

    def _fake_run_transcribe(audio, **kwargs):
        captured["audio"] = audio
        captured["kwargs"] = kwargs
        return core.TranscribeOutput(
            text="hi", language=kwargs["language"], provider=kwargs["provider"],
            diarized=False, segments=[],
        )

    monkeypatch.setattr(core, "run_transcribe", _fake_run_transcribe)

    result = srv.transcribe_audio("a.mp3", language="ru")
    assert result["text"] == "hi"
    assert captured["audio"] == "a.mp3"
    assert captured["kwargs"]["provider"] == "AssemblyAI"  # default
    assert captured["kwargs"]["api_key"] == "test-key"
    assert captured["kwargs"]["language"] == "ru"
    assert captured["kwargs"]["on_status"] is None


def test_missing_openrouter_key_raises(monkeypatch):
    monkeypatch.delenv("VOXNOTE_OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError):
        srv._openrouter_key({})
