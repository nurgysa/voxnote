"""Tests for providers.openai_whisper. HTTP is mocked via unittest.mock."""
from __future__ import annotations

import os
import tempfile
import threading
from unittest.mock import MagicMock, patch

import pytest

from providers import ProviderError
from providers.base import TranscriptionOptions
from providers.openai_whisper import (
    OpenAIWhisperProvider, _MAX_FILE_BYTES, _to_segments,
)
from transcriber import TranscriptionCancelled


@pytest.fixture
def fake_audio():
    f = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    f.write(b"\x00" * 1024)
    f.close()
    yield f.name
    try:
        os.unlink(f.name)
    except OSError:
        pass


@pytest.fixture
def oversized_audio():
    """A 26 MB file — one byte over the OpenAI cap."""
    f = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    f.seek(_MAX_FILE_BYTES + 1)
    f.write(b"\x00")
    f.close()
    yield f.name
    try:
        os.unlink(f.name)
    except OSError:
        pass


# ── construction ──────────────────────────────────────────────────────


def test_rejects_empty_key():
    with pytest.raises(ProviderError, match="ключ OpenAI не задан"):
        OpenAIWhisperProvider("")


def test_advertises_no_diarization_support():
    assert OpenAIWhisperProvider.supports_diarization is False


def test_uses_bearer_header():
    p = OpenAIWhisperProvider("k")
    assert p._headers == {"Authorization": "Bearer k"}


# ── _to_segments adapter ──────────────────────────────────────────────


def test_to_segments_from_verbose_json():
    payload = {
        "language": "russian",
        "segments": [
            {"start": 0.0, "end": 1.5, "text": " Привет мир."},
            {"start": 2.0, "end": 3.0, "text": " Как дела?"},
        ],
    }
    segs = _to_segments(payload)
    assert len(segs) == 2
    # Whisper emits a leading space — adapter strips it.
    assert segs[0]["text"] == "Привет мир."
    assert segs[1]["text"] == "Как дела?"
    # No speaker keys — Whisper doesn't diarize.
    assert all("speaker" not in s for s in segs)


def test_to_segments_falls_back_to_flat_text():
    payload = {"language": "russian", "text": "Просто текст"}
    segs = _to_segments(payload)
    assert segs == [{"start": 0.0, "end": 0.0, "text": "Просто текст"}]


def test_to_segments_empty():
    assert _to_segments({}) == []
    assert _to_segments({"text": ""}) == []


# ── transcribe() — file-size cap, cancel, HTTP errors ────────────────


def test_oversized_file_raises_before_upload(oversized_audio):
    p = OpenAIWhisperProvider("k")
    # Patch should never be reached — the size guard raises first.
    with patch("providers.openai_whisper.requests.post") as mock_post:
        with pytest.raises(ProviderError, match="не более 25 МБ"):
            p.transcribe(oversized_audio, TranscriptionOptions())
    mock_post.assert_not_called()


def test_cancel_before_http(fake_audio):
    p = OpenAIWhisperProvider("key")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(TranscriptionCancelled):
        p.transcribe(
            fake_audio, TranscriptionOptions(), cancel_event=cancel,
        )


def test_missing_file_raises():
    p = OpenAIWhisperProvider("k")
    with pytest.raises(ProviderError, match="Файл не найден"):
        p.transcribe("/no/such/file.mp3", TranscriptionOptions())


def test_401_raises(fake_audio):
    p = OpenAIWhisperProvider("bad-key")
    fake = MagicMock(status_code=401, ok=False, text="Unauthorized")
    with patch("providers.openai_whisper.requests.post", return_value=fake):
        with pytest.raises(ProviderError, match="401"):
            p.transcribe(fake_audio, TranscriptionOptions())


def test_429_raises_rate_limit(fake_audio):
    p = OpenAIWhisperProvider("k")
    fake = MagicMock(status_code=429, ok=False, text="Too many requests")
    with patch("providers.openai_whisper.requests.post", return_value=fake):
        with pytest.raises(ProviderError, match="429"):
            p.transcribe(fake_audio, TranscriptionOptions())


def test_successful_round_trip(fake_audio):
    p = OpenAIWhisperProvider("good-key")
    fake = MagicMock(
        status_code=200, ok=True,
        json=MagicMock(return_value={
            "language": "russian",
            "segments": [
                {"start": 0.0, "end": 1.0, "text": " Привет."},
            ],
        }),
    )
    with patch("providers.openai_whisper.requests.post", return_value=fake):
        result = p.transcribe(
            fake_audio, TranscriptionOptions(language="ru"),
        )
    assert len(result.segments) == 1
    assert result.segments[0]["text"] == "Привет."
    assert "speaker" not in result.segments[0]
