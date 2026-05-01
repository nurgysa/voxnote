"""Tests for providers.speechmatics. HTTP is mocked via unittest.mock."""
from __future__ import annotations

import os
import tempfile
import threading
from unittest.mock import MagicMock, patch

import pytest

from providers import ProviderError
from providers.base import TranscriptionOptions
from providers.speechmatics import (
    SpeechmaticsProvider, _build_config, _normalise_speaker, _to_segments,
)
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
    with pytest.raises(ProviderError, match="ключ Speechmatics не задан"):
        SpeechmaticsProvider("")


def test_uses_bearer_header():
    p = SpeechmaticsProvider("k")
    assert p._headers == {"Authorization": "Bearer k"}


# ── _build_config ─────────────────────────────────────────────────────


def test_build_config_defaults_to_auto_language():
    cfg = _build_config(TranscriptionOptions())
    assert cfg["transcription_config"]["language"] == "auto"
    assert "diarization" not in cfg["transcription_config"]


def test_build_config_sets_diarization_and_vocab():
    cfg = _build_config(TranscriptionOptions(
        language="ru", diarize=True, hotwords=["Нургиса", "Kubernetes"],
    ))
    tc = cfg["transcription_config"]
    assert tc["language"] == "ru"
    assert tc["diarization"] == "speaker"
    assert tc["additional_vocab"] == [
        {"content": "Нургиса"}, {"content": "Kubernetes"},
    ]


# ── _normalise_speaker ────────────────────────────────────────────────


def test_normalise_speaker_S_prefix():
    assert _normalise_speaker("S1") == "SPEAKER_1"
    assert _normalise_speaker("S12") == "SPEAKER_12"


def test_normalise_speaker_unknown_format():
    assert _normalise_speaker("UU") == "SPEAKER_UU"


# ── _to_segments adapter ──────────────────────────────────────────────


def _word(content, start, end, speaker):
    return {
        "type": "word",
        "start_time": start, "end_time": end,
        "alternatives": [{"content": content, "speaker": speaker}],
    }


def _punct(content, start, speaker=None):
    return {
        "type": "punctuation",
        "start_time": start, "end_time": start,
        "alternatives": [{"content": content, "speaker": speaker}],
    }


def test_to_segments_diarized_with_punctuation():
    payload = {"results": [
        _word("Привет", 0.0, 0.4, "S1"),
        _word("мир",    0.5, 0.8, "S1"),
        _punct(".",     0.8, "S1"),
        _word("Как",    1.2, 1.4, "S2"),
        _word("дела",   1.4, 1.7, "S2"),
        _punct("?",     1.7, "S2"),
    ]}
    segs = _to_segments(payload, want_diarization=True)
    assert len(segs) == 2
    assert segs[0]["text"].startswith("Привет мир")
    assert segs[0]["speaker"] == "SPEAKER_1"
    assert segs[1]["speaker"] == "SPEAKER_2"


def test_to_segments_speaker_change_flushes_mid_sentence():
    payload = {"results": [
        _word("Привет", 0.0, 0.4, "S1"),
        _word("и",      0.5, 0.6, "S2"),
    ]}
    segs = _to_segments(payload, want_diarization=True)
    assert len(segs) == 2
    assert [s["speaker"] for s in segs] == ["SPEAKER_1", "SPEAKER_2"]


def test_to_segments_no_diarization():
    payload = {"results": [
        _word("Привет", 0.0, 0.4, "S1"),
        _punct(".",     0.4, "S1"),
    ]}
    segs = _to_segments(payload, want_diarization=False)
    assert len(segs) == 1
    assert "speaker" not in segs[0]


def test_to_segments_empty():
    assert _to_segments({}, want_diarization=True) == []
    assert _to_segments({"results": []}, want_diarization=False) == []


# ── transcribe() — cancel and HTTP errors ─────────────────────────────


def test_cancel_before_http(fake_audio):
    p = SpeechmaticsProvider("key")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(TranscriptionCancelled):
        p.transcribe(
            fake_audio, TranscriptionOptions(), cancel_event=cancel,
        )


def test_missing_file_raises():
    p = SpeechmaticsProvider("k")
    with pytest.raises(ProviderError, match="Файл не найден"):
        p.transcribe("/no/such/file.wav", TranscriptionOptions())


def test_submit_401_raises(fake_audio):
    p = SpeechmaticsProvider("bad-key")
    fake = MagicMock(status_code=401, ok=False, text="Unauthorized")
    with patch("providers.speechmatics.requests.post", return_value=fake):
        with pytest.raises(ProviderError, match="401"):
            p.transcribe(fake_audio, TranscriptionOptions())


def test_successful_round_trip(fake_audio):
    submit_resp = MagicMock(
        status_code=200, ok=True,
        json=MagicMock(return_value={"id": "job-42"}),
    )
    poll_resp = MagicMock(
        status_code=200, ok=True,
        json=MagicMock(return_value={"job": {"status": "done"}}),
    )
    transcript_resp = MagicMock(
        status_code=200, ok=True,
        json=MagicMock(return_value={"results": [
            _word("Привет", 0.0, 0.4, "S1"),
            _punct(".",    0.4, "S1"),
        ]}),
    )

    p = SpeechmaticsProvider("good-key")
    with patch(
        "providers.speechmatics.requests.post", return_value=submit_resp,
    ), patch(
        "providers.speechmatics.requests.get",
        side_effect=[poll_resp, transcript_resp],
    ):
        result = p.transcribe(
            fake_audio,
            TranscriptionOptions(diarize=True, language="ru"),
        )
    assert len(result.segments) == 1
    assert result.segments[0]["speaker"] == "SPEAKER_1"


def test_rejected_status_raises(fake_audio):
    submit_resp = MagicMock(
        status_code=200, ok=True,
        json=MagicMock(return_value={"id": "job-bad"}),
    )
    poll_resp = MagicMock(
        status_code=200, ok=True,
        json=MagicMock(return_value={
            "job": {"status": "rejected", "errors": ["bad audio"]},
        }),
    )
    p = SpeechmaticsProvider("good-key")
    with patch(
        "providers.speechmatics.requests.post", return_value=submit_resp,
    ), patch(
        "providers.speechmatics.requests.get", return_value=poll_resp,
    ), patch(
        "providers.speechmatics.requests.delete",  # best-effort cancel
    ):
        with pytest.raises(ProviderError, match="bad audio"):
            p.transcribe(fake_audio, TranscriptionOptions())
