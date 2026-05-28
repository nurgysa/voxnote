"""Tests for the AssemblyAI cloud transcription provider.

Two test classes worth knowing about:

1. ``_to_segments`` is a pure function — no HTTP. We feed it the four
   shapes the API is documented to return (utterances, words-only,
   bare text, empty) and verify the mapping into our internal segment
   contract.

2. The ``transcribe`` flow tests use ``unittest.mock.patch`` to stub
   out ``requests.post`` / ``requests.get`` / ``requests.delete``. We
   don't run the polling sleep — patching ``time.sleep`` keeps the test
   suite snappy.

These tests catch the things most likely to break:
- AssemblyAI changes their JSON shape → response→segments tests fail
- Bad API key handling stops returning a friendly Russian message
- Cancel during poll forgets to fire the cancel-DELETE
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from providers.assemblyai import AssemblyAIProvider, _to_segments
from providers.base import ProviderError, TranscriptionOptions

# ── Constructor validation ───────────────────────────────────────────


def test_constructor_rejects_empty_api_key():
    with pytest.raises(ProviderError, match="API-ключ AssemblyAI не задан"):
        AssemblyAIProvider("")


def test_constructor_rejects_whitespace_api_key():
    with pytest.raises(ProviderError, match="API-ключ AssemblyAI не задан"):
        AssemblyAIProvider("   \t\n  ")


def test_constructor_strips_surrounding_whitespace_in_key():
    p = AssemblyAIProvider("  abc123  ")
    assert p._headers["authorization"] == "abc123"


# ── _to_segments — pure response→segments mapping ────────────────────


def test_to_segments_diarized_uses_utterances_with_speaker_prefix():
    payload = {
        "utterances": [
            {"start": 0, "end": 1500, "text": "Привет.", "speaker": "A"},
            {"start": 1500, "end": 3200, "text": "Здравствуй.", "speaker": "B"},
        ],
    }
    out = _to_segments(payload, want_diarization=True)
    assert out == [
        {"start": 0.0, "end": 1.5, "text": "Привет.", "speaker": "SPEAKER_A"},
        {"start": 1.5, "end": 3.2, "text": "Здравствуй.", "speaker": "SPEAKER_B"},
    ]


def test_to_segments_diarized_strips_text_whitespace():
    payload = {
        "utterances": [
            {"start": 0, "end": 1000, "text": "  трим  ", "speaker": "A"},
        ],
    }
    out = _to_segments(payload, want_diarization=True)
    assert out[0]["text"] == "трим"


def test_to_segments_no_diarize_splits_words_at_sentence_boundaries():
    payload = {
        "words": [
            {"start": 0,    "end": 500,  "text": "Hello"},
            {"start": 500,  "end": 1000, "text": "world."},
            {"start": 1200, "end": 1700, "text": "Next"},
            {"start": 1700, "end": 2200, "text": "sentence!"},
        ],
    }
    out = _to_segments(payload, want_diarization=False)
    assert len(out) == 2
    assert out[0]["text"] == "Hello world."
    assert out[0]["start"] == 0.0
    assert out[0]["end"] == 1.0
    assert out[1]["text"] == "Next sentence!"
    assert out[1]["start"] == 1.2
    assert out[1]["end"] == 2.2
    # No diarization → no speaker key.
    assert "speaker" not in out[0]
    assert "speaker" not in out[1]


def test_to_segments_no_diarize_flushes_trailing_words_without_punctuation():
    """A final fragment lacking sentence-end punctuation must still be
    emitted — otherwise users lose the last words of every recording."""
    payload = {
        "words": [
            {"start": 0,   "end": 500, "text": "Tail"},
            {"start": 500, "end": 999, "text": "fragment"},
        ],
    }
    out = _to_segments(payload, want_diarization=False)
    assert out == [{"start": 0.0, "end": 0.999, "text": "Tail fragment"}]


def test_to_segments_no_words_falls_back_to_full_text():
    payload = {"text": "Single fallback line."}
    out = _to_segments(payload, want_diarization=False)
    assert out == [{"start": 0.0, "end": 0.0, "text": "Single fallback line."}]


def test_to_segments_empty_payload_returns_empty_list():
    assert _to_segments({}, want_diarization=False) == []
    assert _to_segments({"text": ""}, want_diarization=False) == []
    assert _to_segments({"text": "   "}, want_diarization=False) == []


def test_to_segments_diarize_requested_but_no_utterances_falls_through():
    """If the user asked for diarization but AssemblyAI didn't return
    utterances (e.g. monologue too short to detect), we fall back to the
    no-diarization path rather than crashing."""
    payload = {"text": "Mono speaker only."}
    out = _to_segments(payload, want_diarization=True)
    assert out == [{"start": 0.0, "end": 0.0, "text": "Mono speaker only."}]


# ── _cancel_remote — best-effort DELETE ──────────────────────────────


def test_cancel_remote_logs_request_exception_does_not_raise(caplog):
    """Network errors during cancel must be logged, not propagated — the
    user has already moved on."""
    import logging

    p = AssemblyAIProvider("k")
    import requests
    with patch(
        "providers.assemblyai.requests.delete",
        side_effect=requests.ConnectionError("boom"),
    ), caplog.at_level(logging.WARNING, logger="providers.assemblyai"):
        p._cancel_remote("transcript-123")  # should NOT raise
    assert any("cancel-DELETE failed" in rec.message for rec in caplog.records)
    assert any("transcript-123" in rec.message for rec in caplog.records)


def test_cancel_remote_success_no_log(caplog):
    p = AssemblyAIProvider("k")
    mock_resp = MagicMock(ok=True, status_code=200)
    with patch("providers.assemblyai.requests.delete", return_value=mock_resp):
        with caplog.at_level("WARNING", logger="providers.assemblyai"):
            p._cancel_remote("transcript-456")
    assert caplog.records == []


# ── HTTP error mapping ───────────────────────────────────────────────


def test_upload_401_returns_friendly_russian_error(tmp_path):
    audio = tmp_path / "tiny.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 100)
    p = AssemblyAIProvider("bad-key")

    mock_resp = MagicMock(status_code=401, ok=False, text="auth failed")
    with patch("providers.assemblyai.requests.post", return_value=mock_resp):
        with pytest.raises(ProviderError, match="отклонил ключ"):
            p._upload(str(audio), on_progress=None, cancel_event=None)


def test_upload_network_error_wrapped_in_provider_error(tmp_path):
    audio = tmp_path / "tiny.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 100)
    p = AssemblyAIProvider("k")

    import requests
    with patch(
        "providers.assemblyai.requests.post",
        side_effect=requests.ConnectionError("dns fail"),
    ):
        with pytest.raises(ProviderError, match="Сеть не отвечает"):
            p._upload(str(audio), on_progress=None, cancel_event=None)


def test_upload_returns_url_on_success(tmp_path):
    audio = tmp_path / "tiny.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 100)
    p = AssemblyAIProvider("k")

    mock_resp = MagicMock(status_code=200, ok=True)
    mock_resp.json.return_value = {"upload_url": "https://cdn.aai/abc"}
    with patch("providers.assemblyai.requests.post", return_value=mock_resp):
        url = p._upload(str(audio), on_progress=None, cancel_event=None)
    assert url == "https://cdn.aai/abc"


def test_submit_passes_diarize_and_language_to_payload():
    p = AssemblyAIProvider("k")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["body"] = json
        return MagicMock(status_code=200, ok=True, json=lambda: {"id": "tr-1"})

    with patch("providers.assemblyai.requests.post", side_effect=fake_post):
        tid = p._submit(
            "https://cdn.aai/abc",
            TranscriptionOptions(language="ru", diarize=True, hotwords=["Эппл"]),
        )
    assert tid == "tr-1"
    assert captured["body"]["audio_url"] == "https://cdn.aai/abc"
    assert captured["body"]["speaker_labels"] is True
    assert captured["body"]["language_code"] == "ru"
    assert captured["body"]["word_boost"] == ["Эппл"]
    # No language → language_detection is set instead.
    assert "language_detection" not in captured["body"]


def test_submit_auto_language_sets_language_detection():
    p = AssemblyAIProvider("k")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["body"] = json
        return MagicMock(status_code=200, ok=True, json=lambda: {"id": "tr-2"})

    with patch("providers.assemblyai.requests.post", side_effect=fake_post):
        p._submit("https://cdn.aai/abc", TranscriptionOptions(language=None))
    assert captured["body"]["language_detection"] is True
    assert "language_code" not in captured["body"]


def test_submit_speaker_count_hint_uses_num_then_min():
    p = AssemblyAIProvider("k")
    captured = []

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.append(json)
        return MagicMock(status_code=200, ok=True, json=lambda: {"id": "x"})

    with patch("providers.assemblyai.requests.post", side_effect=fake_post):
        p._submit("u", TranscriptionOptions(num_speakers=3, min_speakers=5))
        p._submit("u", TranscriptionOptions(min_speakers=2))
        p._submit("u", TranscriptionOptions())  # neither
    assert captured[0]["speakers_expected"] == 3   # num_speakers wins
    assert captured[1]["speakers_expected"] == 2   # falls back to min
    assert "speakers_expected" not in captured[2]  # nothing supplied


# ── _poll — completion + error + cancel ──────────────────────────────


def test_poll_returns_completed_payload_immediately():
    p = AssemblyAIProvider("k")
    completed_payload = {"status": "completed", "text": "Hello.", "language_code": "en"}
    mock_resp = MagicMock(ok=True, status_code=200)
    mock_resp.json.return_value = completed_payload

    with patch("providers.assemblyai.requests.get", return_value=mock_resp):
        result = p._poll("tr-1", on_status=None, cancel_event=None)
    assert result is completed_payload


def test_poll_raises_on_error_status():
    p = AssemblyAIProvider("k")
    mock_resp = MagicMock(ok=True, status_code=200)
    mock_resp.json.return_value = {"status": "error", "error": "audio truncated"}

    with patch("providers.assemblyai.requests.get", return_value=mock_resp):
        with pytest.raises(ProviderError, match="audio truncated"):
            p._poll("tr-1", on_status=None, cancel_event=None)


def test_poll_cancel_event_raises_transcription_cancelled():
    p = AssemblyAIProvider("k")
    cancel = threading.Event()
    cancel.set()  # already cancelled before first poll

    # transcriber import is heavy but already paid by test_transcriber_pure.
    from transcriber import TranscriptionCancelled

    with pytest.raises(TranscriptionCancelled):
        p._poll("tr-1", on_status=None, cancel_event=cancel)


def test_poll_status_callback_fires_on_change_only():
    """on_status should be invoked once per distinct status, not every tick.
    With time.sleep patched out, the inner sleep loop runs hot — without the
    "status != last_status" guard we'd get a status callback per iteration."""
    p = AssemblyAIProvider("k")
    statuses = ["queued", "queued", "processing", "completed"]
    responses = [
        MagicMock(ok=True, status_code=200, json=MagicMock(return_value={"status": s}))
        for s in statuses
    ]
    seen: list[str] = []

    with patch("providers.assemblyai.requests.get", side_effect=responses), \
         patch("providers.assemblyai.time.sleep"):
        p._poll("tr-1", on_status=seen.append, cancel_event=None)

    # Only the transitions queued → processing → completed should fire.
    assert seen == [
        "В очереди AssemblyAI...",
        "Обработка на серверах AssemblyAI...",
        "AssemblyAI: completed",
    ]


# ── supports_mixed + language="mixed" branch ─────────────────────────


def test_assemblyai_supports_mixed_true():
    """AssemblyAI explicitly opts in to the KZ+RU+EN code-switching mode.
    Universal-2 covers 99 languages including Kazakh ('kk'), so the opt-in
    is sound. The class attribute must be True (not the inherited default —
    this test guards against accidental revert to the ABC default)."""
    assert AssemblyAIProvider.supports_mixed is True


def test_submit_mixed_uses_multilingual_config():
    """When TranscriptionOptions.language == 'mixed', the submitted body must:
    - set language_detection=True (enable per-file auto language detection)
    - send speech_models=['universal-2'] (REQUIRED on every request since the
      2026-05 AssemblyAI API contract change — singular `speech_model` is
      deprecated per
      https://www.assemblyai.com/docs/api-reference/transcripts/submit)
    - NOT include language_code (mixed mode must not force a single language)
    """
    p = AssemblyAIProvider("test-key")

    submitted_body: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        if json is not None:
            submitted_body.update(json)
        return MagicMock(status_code=200, ok=True, json=lambda: {"id": "tr-mixed"})

    with patch("providers.assemblyai.requests.post", side_effect=fake_post):
        p._submit("https://cdn.aai/mixed.wav", TranscriptionOptions(language="mixed"))

    assert submitted_body.get("language_detection") is True
    assert submitted_body.get("speech_models") == ["universal-2"]
    # speech_model (singular) is the deprecated form — must NOT be sent;
    # AssemblyAI 400s with "must be a non-empty list" if either is wrong.
    assert "speech_model" not in submitted_body
    assert "language_code" not in submitted_body


def test_submit_single_language_includes_required_speech_models():
    """language='ru' must produce a body with:
    - language_code='ru' (single-language path)
    - speech_models=['universal-2'] (REQUIRED — AssemblyAI 2026-05 contract;
      previously this branch sent NO speech_model field, which now 400s with
      "speech_models must be a non-empty list" — see providers/assemblyai.py
      comment for full context)
    - NO language_detection (single language locks detection off)
    - NO singular speech_model (deprecated form)
    """
    p = AssemblyAIProvider("test-key")

    submitted_body: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        if json is not None:
            submitted_body.update(json)
        return MagicMock(status_code=200, ok=True, json=lambda: {"id": "tr-ru"})

    with patch("providers.assemblyai.requests.post", side_effect=fake_post):
        p._submit("https://cdn.aai/ru.wav", TranscriptionOptions(language="ru"))

    assert submitted_body.get("language_code") == "ru"
    # speech_models is required on EVERY request post-2026-05 contract change.
    assert submitted_body.get("speech_models") == ["universal-2"]
    # Mixed-branch keys must not appear; deprecated singular form must not appear.
    assert "language_detection" not in submitted_body
    assert "speech_model" not in submitted_body
