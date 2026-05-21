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
    _MAX_FILE_BYTES,
    OpenAIWhisperProvider,
    _to_segments,
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


# ── supports_mixed + language="mixed" branch ─────────────────────────


def test_openai_whisper_supports_mixed_true():
    """OpenAIWhisperProvider opts in to mixed-mode (class attribute).

    whisper-1 has no native code-switching; supports_mixed=True means the
    provider accepts the sentinel and applies its best-effort path (omit
    the language field so OpenAI auto-detects).  The ABC default is False
    (B.0 flipped it); we explicitly override it back to True here.
    Verified: https://platform.openai.com/docs/api-reference/audio/createTranscription
    — language is an optional field; omitting it enables auto-detection.
    """
    assert OpenAIWhisperProvider.supports_mixed is True


def test_submit_mixed_omits_language_field(fake_audio):
    """`whisper-1` has no native code-switching mode. When language='mixed',
    we omit the language form field so OpenAI's server falls back to
    auto-detect — best-effort, but better than forcing a single language.

    Docs (verified 2026-05-21):
      https://platform.openai.com/docs/api-reference/audio/createTranscription
      "Supplying the input language in ISO-639-1 format can improve accuracy
      and latency." — optional field; omission = auto-detect. No multilingual
      or code_switching flag exists for whisper-1.
    """
    p = OpenAIWhisperProvider("test-key")

    sent_form_keys: set[str] = set()

    def capture_post(url, headers=None, files=None, data=None, timeout=None, **kw):
        # OpenAI uses requests' `data=[(k,v), ...]` for the form;
        # collect the keys actually transmitted.
        if data is not None:
            for k, _v in (data if isinstance(data, list) else data.items()):
                sent_form_keys.add(k)
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.json = lambda: {"text": "", "language": "ru", "segments": []}
        return resp

    with patch("providers.openai_whisper.requests.post", side_effect=capture_post):
        opts = TranscriptionOptions(language="mixed", diarize=False)
        p.transcribe(fake_audio, opts)

    # Critical: language must NOT be in the form when mixed
    assert "language" not in sent_form_keys


def test_submit_single_language_includes_language_field(fake_audio):
    """Regression: language='ru' must still produce ("language", "ru") in the
    form data — the mixed-mode guard must not accidentally suppress it."""
    p = OpenAIWhisperProvider("test-key")

    captured_data: list = []

    def capture_post(url, headers=None, files=None, data=None, timeout=None, **kw):
        if data is not None:
            captured_data.extend(data if isinstance(data, list) else list(data.items()))
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.json = lambda: {"text": "", "language": "russian", "segments": []}
        return resp

    with patch("providers.openai_whisper.requests.post", side_effect=capture_post):
        opts = TranscriptionOptions(language="ru", diarize=False)
        p.transcribe(fake_audio, opts)

    assert ("language", "ru") in captured_data
