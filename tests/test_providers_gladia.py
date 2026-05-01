"""Tests for providers.gladia. HTTP is mocked via unittest.mock."""
from __future__ import annotations

import os
import tempfile
import threading
from unittest.mock import MagicMock, patch

import pytest

from providers import ProviderError
from providers.base import TranscriptionOptions
from providers.gladia import GladiaProvider, _to_segments
from transcriber import TranscriptionCancelled


@pytest.fixture
def fake_audio():
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
    with pytest.raises(ProviderError, match="ключ Gladia не задан"):
        GladiaProvider("")
    with pytest.raises(ProviderError, match="ключ Gladia не задан"):
        GladiaProvider("   ")


def test_uses_x_gladia_key_header():
    p = GladiaProvider("my-key")
    assert p._headers == {"x-gladia-key": "my-key"}


# ── _to_segments adapter ──────────────────────────────────────────────


def _resp(utts):
    return {"result": {"transcription": {"utterances": utts}}}


def test_to_segments_diarized():
    payload = _resp([
        {"start": 0.0, "end": 1.5, "text": "Привет мир.", "speaker": 0},
        {"start": 2.0, "end": 3.0, "text": "Как дела?",   "speaker": 1},
    ])
    segs = _to_segments(payload, want_diarization=True)
    assert len(segs) == 2
    assert segs[0]["text"] == "Привет мир."
    assert segs[0]["speaker"] == "SPEAKER_0"
    assert segs[1]["speaker"] == "SPEAKER_1"


def test_to_segments_no_diarization():
    payload = _resp([
        {"start": 0.0, "end": 1.5, "text": "Привет.", "speaker": 0},
    ])
    segs = _to_segments(payload, want_diarization=False)
    assert len(segs) == 1
    assert "speaker" not in segs[0]


def test_to_segments_empty_payload():
    assert _to_segments({}, want_diarization=True) == []
    assert _to_segments(_resp([]), want_diarization=True) == []


def test_to_segments_falls_back_to_full_transcript():
    payload = {
        "result": {"transcription": {
            "utterances": [],
            "full_transcript": "Просто текст",
        }},
    }
    segs = _to_segments(payload, want_diarization=False)
    assert segs == [{"start": 0.0, "end": 0.0, "text": "Просто текст"}]


# ── transcribe() — cancel and HTTP errors ─────────────────────────────


def test_cancel_before_http(fake_audio):
    p = GladiaProvider("key")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(TranscriptionCancelled):
        p.transcribe(
            fake_audio, TranscriptionOptions(), cancel_event=cancel,
        )


def test_missing_file_raises():
    p = GladiaProvider("key")
    with pytest.raises(ProviderError, match="Файл не найден"):
        p.transcribe("/no/such/file.wav", TranscriptionOptions())


def test_upload_401_raises(fake_audio):
    p = GladiaProvider("bad-key")
    fake = MagicMock()
    fake.status_code = 401
    fake.ok = False
    fake.text = "Unauthorized"
    with patch("providers.gladia.requests.post", return_value=fake):
        with pytest.raises(ProviderError, match="401"):
            p.transcribe(fake_audio, TranscriptionOptions())


def test_successful_three_call_round_trip(fake_audio):
    """Upload → submit → poll(done) → segments."""
    upload_resp = MagicMock(
        status_code=200, ok=True,
        json=MagicMock(return_value={"audio_url": "https://x/audio"}),
    )
    submit_resp = MagicMock(
        status_code=200, ok=True,
        json=MagicMock(return_value={
            "id": "job-1",
            "result_url": "https://x/result/1",
        }),
    )
    final = _resp([
        {"start": 0.0, "end": 1.0, "text": "Привет.", "speaker": 0},
    ])
    final["status"] = "done"
    poll_resp = MagicMock(
        status_code=200, ok=True,
        json=MagicMock(return_value=final),
    )

    p = GladiaProvider("good-key")
    with patch(
        "providers.gladia.requests.post",
        side_effect=[upload_resp, submit_resp],
    ), patch(
        "providers.gladia.requests.get", return_value=poll_resp,
    ):
        result = p.transcribe(
            fake_audio, TranscriptionOptions(diarize=True, language="ru"),
        )
    assert len(result.segments) == 1
    assert result.segments[0]["speaker"] == "SPEAKER_0"


def test_poll_error_status_raises(fake_audio):
    upload_resp = MagicMock(
        status_code=200, ok=True,
        json=MagicMock(return_value={"audio_url": "https://x/audio"}),
    )
    submit_resp = MagicMock(
        status_code=200, ok=True,
        json=MagicMock(return_value={
            "id": "job-1",
            "result_url": "https://x/result/1",
        }),
    )
    poll_resp = MagicMock(
        status_code=200, ok=True,
        json=MagicMock(return_value={
            "status": "error",
            "error_code": "AUDIO_DURATION_TOO_LONG",
        }),
    )
    p = GladiaProvider("good-key")
    with patch(
        "providers.gladia.requests.post",
        side_effect=[upload_resp, submit_resp],
    ), patch(
        "providers.gladia.requests.get", return_value=poll_resp,
    ):
        with pytest.raises(ProviderError, match="AUDIO_DURATION_TOO_LONG"):
            p.transcribe(fake_audio, TranscriptionOptions())
