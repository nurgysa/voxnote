from __future__ import annotations

import types

from cli import core
from cli.app import EXIT_OK, _cmd_pipeline


def _args():
    return types.SimpleNamespace(
        audio="test.m4a",
        provider="AssemblyAI",
        api_key=None,
        openrouter_key="openrouter-key",
        language="ru",
        model=None,
        backend=None,
        container_id=None,
        diarize=False,
        hotwords=None,
        denoise=False,
        quiet=True,
        json=True,
        send=False,
    )


def test_pipeline_uses_provider_specific_env_key(monkeypatch, capsys):
    monkeypatch.setenv("VOXNOTE_API_KEY", "legacy-key")
    monkeypatch.setenv("VOXNOTE_ASSEMBLYAI_API_KEY", "assemblyai-key")

    captured = {}

    def _fake_run_transcribe(audio, **kwargs):
        captured["audio"] = audio
        captured["kwargs"] = kwargs
        return core.TranscribeOutput(
            text="hi", language=kwargs["language"], provider=kwargs["provider"],
            diarized=False, segments=[],
        )

    monkeypatch.setattr("cli.config.base_config", lambda: {})
    monkeypatch.setattr("cli.core.run_transcribe", _fake_run_transcribe)
    monkeypatch.setattr("cli.core.run_extract_tasks", lambda **kwargs: {"tasks": []})
    monkeypatch.setattr(
        "cli.core.run_protocol",
        lambda **kwargs: types.SimpleNamespace(markdown="protocol"),
    )

    code = _cmd_pipeline(_args())

    assert code == EXIT_OK
    assert captured["kwargs"]["provider"] == "AssemblyAI"
    assert captured["kwargs"]["api_key"] == "assemblyai-key"
