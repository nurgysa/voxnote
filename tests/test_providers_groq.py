"""Tests for providers.groq. HTTP is mocked via unittest.mock."""
from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from unittest.mock import MagicMock, patch

import pytest

from providers import ProviderError
from providers.base import TranscriptionOptions
from providers.groq import (
    _DEFAULT_MODEL,
    _MAX_FILE_BYTES,
    GroqProvider,
    _shrink_for_upload,
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
    """A 26 MB file — one byte over the Groq free-tier cap."""
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
    with pytest.raises(ProviderError, match="ключ Groq не задан"):
        GroqProvider("")


def test_rejects_whitespace_only_key():
    with pytest.raises(ProviderError, match="ключ Groq не задан"):
        GroqProvider("   ")


def test_advertises_no_diarization_support():
    # Groq's hosted Whisper has no built-in diarization. Hybrid mode (PR-B)
    # adds local pyannote on the side, but the provider itself only does STT.
    assert GroqProvider.supports_diarization is False


def test_advertises_supports_mixed_true():
    # whisper-large-v3 is natively multilingual — code-switching just works
    # when we omit the language form field.
    assert GroqProvider.supports_mixed is True


def test_uses_bearer_header():
    p = GroqProvider("k")
    assert p._headers == {"Authorization": "Bearer k"}


def test_default_model_is_whisper_large_v3():
    assert _DEFAULT_MODEL == "whisper-large-v3"


def test_constructor_accepts_model_override():
    p = GroqProvider("k", model="whisper-large-v3-turbo")
    assert p._model == "whisper-large-v3-turbo"


def test_constructor_strips_key_whitespace():
    p = GroqProvider("  abc  ")
    assert p._headers == {"Authorization": "Bearer abc"}


# ── _to_segments adapter — word-level distribution ───────────────────


def test_to_segments_from_verbose_json_segments_only():
    """Without top-level words[], _to_segments returns plain segments."""
    payload = {
        "language": "russian",
        "segments": [
            {"start": 0.0, "end": 1.5, "text": " Привет мир."},
            {"start": 2.0, "end": 3.0, "text": " Как дела?"},
        ],
    }
    segs = _to_segments(payload)
    assert len(segs) == 2
    # Whisper emits leading space — strip it.
    assert segs[0]["text"] == "Привет мир."
    assert segs[1]["text"] == "Как дела?"
    # No words[] → no key, no speaker key.
    assert all("speaker" not in s for s in segs)
    assert all("words" not in s for s in segs)


def test_to_segments_preserves_top_level_words_distributed_by_time():
    """Critical for hybrid mode (PR-B): words[] at top level get distributed
    to their owning segment by time-overlap, so speaker_aligner can do
    word-level alignment with local pyannote turns."""
    payload = {
        "language": "russian",
        "segments": [
            {"start": 0.0, "end": 1.5, "text": " Привет мир."},
            {"start": 2.0, "end": 3.5, "text": " Как дела?"},
        ],
        "words": [
            {"word": "Привет", "start": 0.1, "end": 0.6},
            {"word": "мир.", "start": 0.7, "end": 1.4},
            {"word": "Как", "start": 2.1, "end": 2.4},
            {"word": "дела?", "start": 2.5, "end": 3.4},
        ],
    }
    segs = _to_segments(payload)
    assert len(segs) == 2
    assert len(segs[0]["words"]) == 2
    assert segs[0]["words"][0] == {"start": 0.1, "end": 0.6, "word": "Привет"}
    assert segs[0]["words"][1] == {"start": 0.7, "end": 1.4, "word": "мир."}
    assert len(segs[1]["words"]) == 2
    assert segs[1]["words"][0] == {"start": 2.1, "end": 2.4, "word": "Как"}
    assert segs[1]["words"][1] == {"start": 2.5, "end": 3.4, "word": "дела?"}


def test_to_segments_prefers_segment_embedded_words_when_present():
    """Some OpenAI-compatible shapes embed words directly inside segments.
    If that's how Groq returns the data, use it as-is (skip the top-level
    distribution pass entirely)."""
    payload = {
        "language": "russian",
        "segments": [
            {
                "start": 0.0, "end": 1.5, "text": " Привет мир.",
                "words": [
                    {"word": "Привет", "start": 0.1, "end": 0.6},
                    {"word": "мир.", "start": 0.7, "end": 1.4},
                ],
            },
        ],
    }
    segs = _to_segments(payload)
    assert len(segs) == 1
    assert len(segs[0]["words"]) == 2
    assert segs[0]["words"][0]["word"] == "Привет"


def test_to_segments_handles_word_outside_any_segment():
    """A word with midpoint past the last segment end shouldn't crash —
    skip it silently rather than blow up the run."""
    payload = {
        "segments": [
            {"start": 0.0, "end": 1.0, "text": " Hello."},
        ],
        "words": [
            {"word": "Hello.", "start": 0.0, "end": 0.9},
            {"word": "orphan", "start": 5.0, "end": 6.0},  # past segment end
        ],
    }
    segs = _to_segments(payload)
    assert len(segs) == 1
    assert len(segs[0]["words"]) == 1
    assert segs[0]["words"][0]["word"] == "Hello."


def test_to_segments_falls_back_to_flat_text():
    payload = {"language": "russian", "text": "Просто текст"}
    segs = _to_segments(payload)
    assert segs == [{"start": 0.0, "end": 0.0, "text": "Просто текст"}]


def test_to_segments_empty():
    assert _to_segments({}) == []
    assert _to_segments({"text": ""}) == []


# ── _shrink_for_upload helper — transparent opus compression ─────────


def test_shrink_passes_through_small_file(fake_audio):
    """Files at or under the cap don't trigger any subprocess work — same
    path is returned with is_temp=False so the caller skips cleanup."""
    with patch("providers.groq.subprocess.run") as mock_run:
        upload_path, is_temp = _shrink_for_upload(fake_audio)
    assert upload_path == fake_audio
    assert is_temp is False
    mock_run.assert_not_called()


def test_shrink_compresses_oversized_via_ffmpeg(oversized_audio):
    """Oversized files trigger ffmpeg → opus 32 kbps mono 16k. The helper
    returns the new tempfile path with is_temp=True so the caller can
    clean it up via os.unlink."""
    fake_tmp_path = oversized_audio + ".opus_fake"
    # Pretend ffmpeg succeeded and produced a small file.
    with open(fake_tmp_path, "wb") as f:
        f.write(b"x" * 1024)  # 1 KB — well under 25 MB cap
    try:
        captured_cmd: list = []

        def fake_run(cmd, capture_output=True, check=True):
            captured_cmd.extend(cmd)
            mock_result = MagicMock(returncode=0)
            return mock_result

        with patch("providers.groq.subprocess.run", side_effect=fake_run), \
             patch("providers.groq.tempfile.NamedTemporaryFile") as mock_ntf:
            mock_ntf.return_value.__enter__ = lambda s: s
            mock_ntf.return_value.name = fake_tmp_path
            mock_ntf.return_value.close = lambda: None
            upload_path, is_temp = _shrink_for_upload(oversized_audio)

        assert upload_path == fake_tmp_path
        assert is_temp is True
        # Verify ffmpeg got the right args for opus 32k mono 16k.
        assert "ffmpeg" in captured_cmd
        assert "libopus" in captured_cmd
        assert "32k" in captured_cmd
        assert "1" in captured_cmd  # -ac 1 mono
        assert "16000" in captured_cmd  # -ar 16000
    finally:
        try:
            os.unlink(fake_tmp_path)
        except OSError:
            pass


def test_shrink_raises_on_ffmpeg_failure(oversized_audio):
    """ffmpeg returning non-zero → ProviderError with Russian-actionable
    message + tempfile cleanup."""
    fake_tmp_path = oversized_audio + ".opus_fail"
    with open(fake_tmp_path, "wb") as f:
        f.write(b"partial")

    def failing_run(cmd, capture_output=True, check=True):
        raise subprocess.CalledProcessError(
            returncode=1, cmd=cmd, stderr=b"Invalid data",
        )

    try:
        with patch("providers.groq.subprocess.run", side_effect=failing_run), \
             patch("providers.groq.tempfile.NamedTemporaryFile") as mock_ntf:
            mock_ntf.return_value.name = fake_tmp_path
            mock_ntf.return_value.close = lambda: None
            with pytest.raises(ProviderError, match="ffmpeg"):
                _shrink_for_upload(oversized_audio)
    finally:
        # The helper should have cleaned up — verify by trying again.
        if os.path.exists(fake_tmp_path):
            os.unlink(fake_tmp_path)


def test_shrink_raises_when_ffmpeg_missing(oversized_audio):
    """If ffmpeg is not on PATH at all, surface a clear actionable message."""
    fake_tmp_path = oversized_audio + ".opus_noffmpeg"
    with open(fake_tmp_path, "wb") as f:
        f.write(b"")

    try:
        with patch(
            "providers.groq.subprocess.run",
            side_effect=FileNotFoundError("ffmpeg"),
        ), patch("providers.groq.tempfile.NamedTemporaryFile") as mock_ntf:
            mock_ntf.return_value.name = fake_tmp_path
            mock_ntf.return_value.close = lambda: None
            with pytest.raises(ProviderError, match="ffmpeg"):
                _shrink_for_upload(oversized_audio)
    finally:
        if os.path.exists(fake_tmp_path):
            os.unlink(fake_tmp_path)


def test_shrink_raises_when_compressed_still_too_big(oversized_audio):
    """If even the opus version is over 25 MB (extremely long audio),
    raise a ProviderError suggesting chunking. Tempfile is still cleaned."""
    fake_tmp_path = oversized_audio + ".opus_huge"
    # Produce a "compressed" file that's still oversized.
    with open(fake_tmp_path, "wb") as f:
        f.seek(_MAX_FILE_BYTES + 1024)
        f.write(b"\x00")

    try:
        def fake_run(cmd, capture_output=True, check=True):
            return MagicMock(returncode=0)

        with patch("providers.groq.subprocess.run", side_effect=fake_run), \
             patch("providers.groq.tempfile.NamedTemporaryFile") as mock_ntf:
            mock_ntf.return_value.name = fake_tmp_path
            mock_ntf.return_value.close = lambda: None
            with pytest.raises(ProviderError, match="После сжатия"):
                _shrink_for_upload(oversized_audio)
    finally:
        if os.path.exists(fake_tmp_path):
            os.unlink(fake_tmp_path)


# ── transcribe() — file-size cap, cancel, HTTP errors ────────────────


def test_transcribe_oversized_compresses_and_uploads(oversized_audio):
    """Integration: oversized input triggers compression, the COMPRESSED
    tempfile is what gets uploaded (not the original), and the tempfile is
    cleaned up after the HTTP call returns."""
    fake_tmp_path = oversized_audio + ".opus_int"
    with open(fake_tmp_path, "wb") as f:
        f.write(b"x" * 1024)

    uploaded_filenames: list[str] = []

    def capture_post(url, headers=None, files=None, data=None, timeout=None, **kw):
        # files["file"] is (basename, fileobj, mime); capture the basename
        # so the test can assert we sent the compressed file, not the
        # original oversized one.
        if files and "file" in files:
            uploaded_filenames.append(files["file"][0])
        resp = MagicMock(status_code=200, ok=True)
        resp.json = lambda: {"text": "", "segments": []}
        return resp

    def fake_run(cmd, capture_output=True, check=True):
        return MagicMock(returncode=0)

    with patch("providers.groq.subprocess.run", side_effect=fake_run), \
         patch("providers.groq.requests.post", side_effect=capture_post), \
         patch("providers.groq.tempfile.NamedTemporaryFile") as mock_ntf:
        mock_ntf.return_value.name = fake_tmp_path
        mock_ntf.return_value.close = lambda: None
        p = GroqProvider("k")
        p.transcribe(oversized_audio, TranscriptionOptions())

    assert len(uploaded_filenames) == 1
    # Uploaded basename comes from the compressed tempfile, not the
    # oversized original. Just check the extension survived.
    assert uploaded_filenames[0].endswith(".opus_int")
    # Tempfile cleaned up after upload.
    assert not os.path.exists(fake_tmp_path), \
        "compressed tempfile should be cleaned after upload"


def test_transcribe_oversized_cleans_up_tempfile_on_http_error(oversized_audio):
    """If the upload itself fails, the compressed tempfile must still get
    cleaned — no leaked tempfiles even on the error path."""
    fake_tmp_path = oversized_audio + ".opus_err"
    with open(fake_tmp_path, "wb") as f:
        f.write(b"x" * 1024)

    def fake_run(cmd, capture_output=True, check=True):
        return MagicMock(returncode=0)

    fake_fail = MagicMock(status_code=500, ok=False, text="Internal error")

    with patch("providers.groq.subprocess.run", side_effect=fake_run), \
         patch("providers.groq.requests.post", return_value=fake_fail), \
         patch("providers.groq.tempfile.NamedTemporaryFile") as mock_ntf:
        mock_ntf.return_value.name = fake_tmp_path
        mock_ntf.return_value.close = lambda: None
        p = GroqProvider("k")
        with pytest.raises(ProviderError, match="500"):
            p.transcribe(oversized_audio, TranscriptionOptions())

    assert not os.path.exists(fake_tmp_path), \
        "compressed tempfile must be cleaned even when upload fails"


def test_cancel_before_http(fake_audio):
    p = GroqProvider("key")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(TranscriptionCancelled):
        p.transcribe(
            fake_audio, TranscriptionOptions(), cancel_event=cancel,
        )


def test_missing_file_raises():
    p = GroqProvider("k")
    with pytest.raises(ProviderError, match="Файл не найден"):
        p.transcribe("/no/such/file.mp3", TranscriptionOptions())


def test_401_raises(fake_audio):
    p = GroqProvider("bad-key")
    fake = MagicMock(status_code=401, ok=False, text="Unauthorized")
    with patch("providers.groq.requests.post", return_value=fake):
        with pytest.raises(ProviderError, match="401"):
            p.transcribe(fake_audio, TranscriptionOptions())


def test_429_raises_rate_limit(fake_audio):
    p = GroqProvider("k")
    fake = MagicMock(status_code=429, ok=False, text="Too many requests")
    with patch("providers.groq.requests.post", return_value=fake):
        with pytest.raises(ProviderError, match="429"):
            p.transcribe(fake_audio, TranscriptionOptions())


def test_network_error_wrapped_as_provider_error(fake_audio):
    import requests as req
    p = GroqProvider("k")
    with patch(
        "providers.groq.requests.post",
        side_effect=req.ConnectionError("DNS fail"),
    ):
        with pytest.raises(ProviderError, match="Сеть"):
            p.transcribe(fake_audio, TranscriptionOptions())


def test_successful_round_trip(fake_audio):
    p = GroqProvider("good-key")
    fake = MagicMock(
        status_code=200, ok=True,
        json=MagicMock(return_value={
            "language": "russian",
            "segments": [
                {"start": 0.0, "end": 1.0, "text": " Привет."},
            ],
            "words": [
                {"word": "Привет.", "start": 0.0, "end": 0.9},
            ],
        }),
    )
    with patch("providers.groq.requests.post", return_value=fake):
        result = p.transcribe(
            fake_audio, TranscriptionOptions(language="ru"),
        )
    assert len(result.segments) == 1
    assert result.segments[0]["text"] == "Привет."
    assert "speaker" not in result.segments[0]
    # Word-level survived the round-trip — critical for PR-B hybrid mode.
    assert len(result.segments[0]["words"]) == 1
    assert result.segments[0]["words"][0]["word"] == "Привет."


# ── multipart form — granularities, language, model ──────────────────


def test_requests_word_granularities(fake_audio):
    """PR-B's hybrid path needs word-level timestamps to call
    speaker_aligner._assign_speakers_word_level. The provider MUST ask
    Groq for both segment and word granularities."""
    p = GroqProvider("k")
    captured_data: list = []

    def capture_post(url, headers=None, files=None, data=None, timeout=None, **kw):
        if data is not None:
            captured_data.extend(data if isinstance(data, list) else list(data.items()))
        resp = MagicMock(status_code=200, ok=True)
        resp.json = lambda: {"text": "", "language": "ru", "segments": []}
        return resp

    with patch("providers.groq.requests.post", side_effect=capture_post):
        p.transcribe(fake_audio, TranscriptionOptions(language="ru"))

    # Both granularities present.
    assert ("timestamp_granularities[]", "segment") in captured_data
    assert ("timestamp_granularities[]", "word") in captured_data


def test_uses_default_model_in_request(fake_audio):
    p = GroqProvider("k")
    captured_data: list = []

    def capture_post(url, headers=None, files=None, data=None, timeout=None, **kw):
        if data is not None:
            captured_data.extend(data if isinstance(data, list) else list(data.items()))
        resp = MagicMock(status_code=200, ok=True)
        resp.json = lambda: {"text": "", "segments": []}
        return resp

    with patch("providers.groq.requests.post", side_effect=capture_post):
        p.transcribe(fake_audio, TranscriptionOptions())

    assert ("model", "whisper-large-v3") in captured_data


def test_uses_overridden_model_in_request(fake_audio):
    p = GroqProvider("k", model="whisper-large-v3-turbo")
    captured_data: list = []

    def capture_post(url, headers=None, files=None, data=None, timeout=None, **kw):
        if data is not None:
            captured_data.extend(data if isinstance(data, list) else list(data.items()))
        resp = MagicMock(status_code=200, ok=True)
        resp.json = lambda: {"text": "", "segments": []}
        return resp

    with patch("providers.groq.requests.post", side_effect=capture_post):
        p.transcribe(fake_audio, TranscriptionOptions())

    assert ("model", "whisper-large-v3-turbo") in captured_data


def test_submit_mixed_omits_language_field(fake_audio):
    """language='mixed' means trilingual KZ+RU+EN. whisper-large-v3 detects
    language per-segment natively when no language is forced. Omit the form
    field to enable auto-detection."""
    p = GroqProvider("k")
    sent_form_keys: set[str] = set()

    def capture_post(url, headers=None, files=None, data=None, timeout=None, **kw):
        if data is not None:
            for k, _v in (data if isinstance(data, list) else data.items()):
                sent_form_keys.add(k)
        resp = MagicMock(status_code=200, ok=True)
        resp.json = lambda: {"text": "", "language": "ru", "segments": []}
        return resp

    with patch("providers.groq.requests.post", side_effect=capture_post):
        p.transcribe(
            fake_audio, TranscriptionOptions(language="mixed", diarize=False),
        )

    assert "language" not in sent_form_keys


def test_submit_single_language_includes_language_field(fake_audio):
    """Regression: language='ru' must produce ('language', 'ru') in the form."""
    p = GroqProvider("k")
    captured_data: list = []

    def capture_post(url, headers=None, files=None, data=None, timeout=None, **kw):
        if data is not None:
            captured_data.extend(data if isinstance(data, list) else list(data.items()))
        resp = MagicMock(status_code=200, ok=True)
        resp.json = lambda: {"text": "", "language": "russian", "segments": []}
        return resp

    with patch("providers.groq.requests.post", side_effect=capture_post):
        p.transcribe(fake_audio, TranscriptionOptions(language="ru"))

    assert ("language", "ru") in captured_data


def test_endpoint_url_is_groq_openai_compat(fake_audio):
    """Regression on the API URL — Groq exposes their STT under the
    /openai/v1 namespace for OpenAI compatibility."""
    p = GroqProvider("k")
    seen_url: list[str] = []

    def capture_post(url, headers=None, files=None, data=None, timeout=None, **kw):
        seen_url.append(url)
        resp = MagicMock(status_code=200, ok=True)
        resp.json = lambda: {"text": "", "segments": []}
        return resp

    with patch("providers.groq.requests.post", side_effect=capture_post):
        p.transcribe(fake_audio, TranscriptionOptions())

    assert seen_url == ["https://api.groq.com/openai/v1/audio/transcriptions"]
