"""Tests for providers.deepgram. HTTP is mocked via unittest.mock."""
from __future__ import annotations

import os
import tempfile
import threading
from unittest.mock import MagicMock, patch

import pytest

from providers import ProviderError
from providers.base import TranscriptionOptions
from providers.deepgram import (
    DeepgramProvider,
    _build_params,
    _to_segments,
)
from transcriber import TranscriptionCancelled


@pytest.fixture
def fake_audio():
    """Tiny temp file standing in for an audio path."""
    f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    f.write(b"\x00" * 1024)
    f.close()
    yield f.name
    try:
        os.unlink(f.name)
    except OSError:
        pass


# ── construction ──────────────────────────────────────────────────────


def test_rejects_empty_key():
    with pytest.raises(ProviderError, match="ключ Deepgram не задан"):
        DeepgramProvider("")
    with pytest.raises(ProviderError, match="ключ Deepgram не задан"):
        DeepgramProvider("   ")


def test_accepts_whitespace_around_key():
    p = DeepgramProvider("  abc  ")
    assert p._api_key == "abc"


# ── _build_params ─────────────────────────────────────────────────────


def test_build_params_minimal():
    params = _build_params(TranscriptionOptions())
    keys = [k for k, _ in params]
    assert ("model", "nova-3") in params
    assert ("punctuate", "true") in params
    # No language → auto-detect
    assert ("detect_language", "true") in params
    assert "diarize" not in keys


def test_build_params_full():
    opts = TranscriptionOptions(
        language="ru", diarize=True,
        hotwords=["Нургиса", "Kubernetes"],
    )
    params = _build_params(opts)
    assert ("language", "ru") in params
    assert ("diarize", "true") in params
    assert ("keywords", "Нургиса:1") in params
    assert ("keywords", "Kubernetes:1") in params


# ── _to_segments adapter ──────────────────────────────────────────────


def _resp(words):
    return {"results": {"channels": [{"alternatives": [{"words": words}]}]}}


def test_to_segments_diarized_speaker_change():
    payload = _resp([
        {"punctuated_word": "Привет",  "start": 0.0, "end": 0.4, "speaker": 0},
        {"punctuated_word": "мир.",    "start": 0.4, "end": 0.7, "speaker": 0},
        {"punctuated_word": "Как",     "start": 1.0, "end": 1.2, "speaker": 1},
        {"punctuated_word": "дела?",   "start": 1.2, "end": 1.6, "speaker": 1},
    ])
    segs = _to_segments(payload, want_diarization=True)
    assert len(segs) == 2
    assert segs[0]["text"] == "Привет мир."
    assert segs[0]["speaker"] == "SPEAKER_0"
    assert segs[0]["start"] == pytest.approx(0.0)
    assert segs[0]["end"] == pytest.approx(0.7)
    assert segs[1]["text"] == "Как дела?"
    assert segs[1]["speaker"] == "SPEAKER_1"


def test_to_segments_diarized_breaks_on_speaker_mid_sentence():
    """Speaker change without sentence-ending punctuation still flushes."""
    payload = _resp([
        {"punctuated_word": "Привет", "start": 0.0, "end": 0.4, "speaker": 0},
        {"punctuated_word": "и",      "start": 0.5, "end": 0.6, "speaker": 1},
    ])
    segs = _to_segments(payload, want_diarization=True)
    assert len(segs) == 2
    assert [s["speaker"] for s in segs] == ["SPEAKER_0", "SPEAKER_1"]


def test_to_segments_no_diarization_omits_speaker_key():
    payload = _resp([
        {"punctuated_word": "Привет.", "start": 0.0, "end": 0.5},
    ])
    segs = _to_segments(payload, want_diarization=False)
    assert len(segs) == 1
    assert "speaker" not in segs[0]


def test_to_segments_empty_payload():
    assert _to_segments({}, want_diarization=True) == []
    assert _to_segments(
        {"results": {"channels": []}}, want_diarization=False,
    ) == []


def test_to_segments_falls_back_to_transcript_when_no_words():
    payload = {
        "results": {"channels": [{"alternatives": [{
            "transcript": "Просто текст",
            "words": [],
        }]}]},
    }
    segs = _to_segments(payload, want_diarization=False)
    assert segs == [{"start": 0.0, "end": 0.0, "text": "Просто текст"}]


# ── transcribe() — cancel and HTTP error mapping ──────────────────────


def test_cancel_before_http_raises(fake_audio):
    p = DeepgramProvider("key")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(TranscriptionCancelled):
        p.transcribe(
            fake_audio, TranscriptionOptions(), cancel_event=cancel,
        )


def test_missing_file_raises_provider_error():
    p = DeepgramProvider("key")
    with pytest.raises(ProviderError, match="Файл не найден"):
        p.transcribe("/no/such/path.wav", TranscriptionOptions())


def test_401_raises_provider_error(fake_audio):
    p = DeepgramProvider("bad-key")
    fake = MagicMock()
    fake.status_code = 401
    fake.ok = False
    fake.text = "Unauthorized"
    with patch("providers.deepgram.requests.post", return_value=fake):
        with pytest.raises(ProviderError, match="401"):
            p.transcribe(fake_audio, TranscriptionOptions())


def test_successful_diarized_round_trip(fake_audio):
    p = DeepgramProvider("good-key")
    fake = MagicMock()
    fake.status_code = 200
    fake.ok = True
    fake.json.return_value = _resp([
        {"punctuated_word": "Привет.", "start": 0.0, "end": 0.5, "speaker": 0},
    ])
    with patch("providers.deepgram.requests.post", return_value=fake):
        result = p.transcribe(
            fake_audio, TranscriptionOptions(diarize=True),
        )
    assert len(result.segments) == 1
    assert result.segments[0]["speaker"] == "SPEAKER_0"


# ── supports_mixed = False + early-raise ──────────────────────────────


def test_deepgram_supports_mixed_false():
    """Deepgram nova-3 doesn't include Kazakh; reflect that as a
    capability the UI and runtime guard can read."""
    assert DeepgramProvider.supports_mixed is False


def test_submit_mixed_raises_provider_error_before_http(fake_audio):
    """When called with language='mixed', Deepgram must raise BEFORE
    making any HTTP request. Defense-in-depth: the transcribe() cloud
    short-circuit (B.0) is the primary block; this is the secondary
    block for callers using DeepgramProvider directly."""
    p = DeepgramProvider("test-key")
    with patch("providers.deepgram.requests.post") as mock_post:
        opts = TranscriptionOptions(language="mixed", diarize=False)
        with pytest.raises(ProviderError, match="Қазақша"):
            p.transcribe(fake_audio, opts)
        assert mock_post.call_count == 0
