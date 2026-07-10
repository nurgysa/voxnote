from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from providers.base import ProviderError, TranscriptionOptions
from providers.groq import (
    DEFAULT_MODEL,
    GroqProvider,
    _build_form_data,
    _to_segments,
)


def test_constructor_rejects_empty_api_key():
    with pytest.raises(ProviderError, match="API-ключ Groq не задан"):
        GroqProvider("")


def test_max_upload_bytes_is_25_mib_free_tier_cap():
    assert GroqProvider.max_upload_bytes == 25 * 1024 * 1024


def test_provider_is_asr_only_and_supports_mixed_auto_detection():
    assert GroqProvider.supports_diarization is False
    assert GroqProvider.supports_mixed is True


def test_build_form_data_uses_turbo_verbose_json_and_segment_timestamps():
    data = _build_form_data(
        TranscriptionOptions(language="ru", hotwords=["VoxNote", "Mini-AGI"])
    )

    assert ("model", DEFAULT_MODEL) in data
    assert ("response_format", "verbose_json") in data
    assert ("timestamp_granularities[]", "segment") in data
    assert ("temperature", "0") in data
    assert ("language", "ru") in data
    assert ("prompt", "VoxNote, Mini-AGI") in data


def test_build_form_data_mixed_omits_literal_mixed_language_and_prompts_context():
    data = _build_form_data(TranscriptionOptions(language="mixed"))

    assert ("language", "mixed") not in data
    assert any(k == "prompt" and "Kazakh" in v and "Russian" in v for k, v in data)


def test_to_segments_prefers_verbose_segments_without_speaker_labels():
    payload = {
        "language": "ru",
        "segments": [
            {"start": 0.0, "end": 1.2, "text": " Привет. "},
            {"start": 1.2, "end": 2.5, "text": "Как дела?"},
        ],
    }

    assert _to_segments(payload) == [
        {"start": 0.0, "end": 1.2, "text": "Привет."},
        {"start": 1.2, "end": 2.5, "text": "Как дела?"},
    ]


def test_to_segments_falls_back_to_text():
    assert _to_segments({"text": "Single transcript."}) == [
        {"start": 0.0, "end": 0.0, "text": "Single transcript."}
    ]


def test_transcribe_rejects_diarization_before_http(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF" + b"\0" * 16)

    with patch("providers._common.requests.post") as post:
        with pytest.raises(ProviderError, match="ASR-only"):
            GroqProvider("k").transcribe(
                str(audio), TranscriptionOptions(diarize=True)
            )

    post.assert_not_called()


def test_transcribe_posts_multipart_audio_and_returns_model(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF" + b"\0" * 16)
    captured = {}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        captured.update(
            {"url": url, "headers": headers, "files": files, "data": data, "timeout": timeout}
        )
        return MagicMock(
            status_code=200,
            ok=True,
            json=lambda: {
                "language": "ru",
                "segments": [{"start": 0, "end": 1, "text": "Hi"}],
            },
            text='{"ok":true}',
        )

    with patch("providers._common.requests.post", side_effect=fake_post):
        out = GroqProvider("secret").transcribe(
            str(audio), TranscriptionOptions(language="ru")
        )

    assert captured["url"] == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["data"] == _build_form_data(TranscriptionOptions(language="ru"))
    file_tuple = captured["files"]["file"]
    assert file_tuple[0] == "a.wav"
    assert file_tuple[2] == "audio/wav"
    assert out.segments == [{"start": 0.0, "end": 1.0, "text": "Hi"}]
    assert out.language == "ru"
    assert out.model == DEFAULT_MODEL
