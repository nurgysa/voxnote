"""Tests for transcriber.cloud_chunker.

The chunker has both pure functions (_pick_split_points,
_merge_chunk_results) and I/O-bound ones (_find_silence_boundaries,
_extract_chunk, _audio_duration). Pure functions get direct unit tests;
I/O paths get integration-style tests where the ffmpeg subprocess and
provider.transcribe are mocked.

Strategy: avoid creating real long audio files. We feed the chunker
mocked duration/silence data and verify the choreography
(splits, offsets, cleanup, progress callbacks, cancel).
"""
from __future__ import annotations

import os
import tempfile
import threading
from unittest.mock import MagicMock, patch

import pytest

from providers import ProviderError
from providers.base import (
    TranscriptionOptions,
    TranscriptionProvider,
    TranscriptionResult,
)
from transcriber import TranscriptionCancelled
from transcriber.cloud_chunker import (
    _merge_chunk_results,
    _pick_split_points,
    needs_chunking,
    transcribe_chunked,
)

# ── fake provider used by integration tests ──────────────────────────


class _StubProvider(TranscriptionProvider):
    """Records calls + returns canned TranscriptionResults per chunk.

    Index in ``self._results`` ↔ call number. Pass enough results for
    your test's expected chunk count.
    """

    display_name = "Stub"
    supports_diarization = False
    supports_mixed = True
    max_upload_bytes = 25 * 1024 * 1024

    def __init__(self, results: list[TranscriptionResult]):
        self._results = list(results)
        self.calls: list[dict] = []

    def transcribe(
        self,
        audio_path,
        options,
        on_status=None,
        on_progress=None,
        cancel_event=None,
    ) -> TranscriptionResult:
        self.calls.append({
            "audio_path": audio_path,
            "options": options,
            "cancel_event": cancel_event,
        })
        if cancel_event is not None and cancel_event.is_set():
            raise TranscriptionCancelled()
        if not self._results:
            raise AssertionError(
                f"_StubProvider called {len(self.calls)} times but only "
                f"seeded with {len(self.calls) - 1} results"
            )
        return self._results.pop(0)


def _make_result(segs: list[dict], language: str | None = "ru") -> TranscriptionResult:
    return TranscriptionResult(segments=segs, language=language, raw={})


@pytest.fixture
def real_wav_tempfile():
    """A trivially-small WAV that satisfies os.path.exists checks but
    isn't actually read for content by the mocked _audio_duration."""
    f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    f.write(b"RIFF\x00\x00\x00\x00WAVE")
    f.close()
    yield f.name
    try:
        os.unlink(f.name)
    except OSError:
        pass


# ── _pick_split_points (pure function) ───────────────────────────────


def test_pick_split_points_no_split_for_short_audio():
    """Audio under the target chunk size returns an empty split list."""
    # 1h = 3600s, target = 5400s (90 min) → no split needed
    points = _pick_split_points(
        silences=[], total_duration=3600.0,
        target_chunk_seconds=5400.0, tolerance=300.0,
    )
    assert points == []


def test_pick_split_points_picks_silence_near_target():
    """3h audio with a silence right at 90 min → one split at the
    midpoint of that silence."""
    silences = [
        (89.0 * 60, 89.0 * 60 + 5),       # 5s silence at 89 min
        (90.5 * 60, 90.5 * 60 + 3),       # 3s silence at 90.5 min
        (91.0 * 60, 91.0 * 60 + 8),       # 8s silence at 91 min (longest)
    ]
    points = _pick_split_points(
        silences=silences, total_duration=3 * 3600.0,
        target_chunk_seconds=5400.0, tolerance=300.0,
    )
    # Picks the LONGEST silence within ±5 min of target (5400s = 90 min).
    # All three are within tolerance; 91-min silence is longest (8s).
    # Split point = midpoint of that silence = 91*60 + 4 = 5464s
    assert len(points) == 1
    assert points[0] == pytest.approx(91 * 60 + 4.0)


def test_pick_split_points_falls_back_to_hard_cut_when_no_silence_in_window():
    """If no silence within tolerance, fall back to hard cut at target."""
    # 3h audio, silence only at start (5..10s), nothing near 90 min
    silences = [(5.0, 10.0)]
    points = _pick_split_points(
        silences=silences, total_duration=3 * 3600.0,
        target_chunk_seconds=5400.0, tolerance=300.0,
    )
    assert points == [5400.0]


def test_pick_split_points_multiple_chunks_for_5h_audio():
    """5h audio (18000s) at target=5400s (90min) needs 3 splits → 4 chunks
    (5h ÷ 90min = 3.33, so 4 chunks @ ~75min average). With silences
    only near the first two split points, the third falls back to hard
    cut at exactly 16200s (3 × 5400)."""
    silences = [
        (89.0 * 60, 89.0 * 60 + 10),   # near 90-min mark (5400s)
        (179.0 * 60, 179.0 * 60 + 8),  # near 180-min mark (10800s)
    ]
    points = _pick_split_points(
        silences=silences, total_duration=5 * 3600.0,
        target_chunk_seconds=5400.0, tolerance=300.0,
    )
    assert len(points) == 3
    assert points[0] == pytest.approx(89 * 60 + 5.0)
    assert points[1] == pytest.approx(179 * 60 + 4.0)
    # Third split: no silence near 270min target → hard cut at 16200s.
    assert points[2] == pytest.approx(16200.0)


def test_pick_split_points_emits_strictly_increasing_points():
    """Sanity: split points must be monotonically increasing —
    otherwise we'd produce overlapping or zero-length chunks."""
    silences = [
        (88.0 * 60, 88.0 * 60 + 5),
        (180.0 * 60, 180.0 * 60 + 5),
        (270.0 * 60, 270.0 * 60 + 5),
    ]
    points = _pick_split_points(
        silences=silences, total_duration=6 * 3600.0,
        target_chunk_seconds=5400.0, tolerance=600.0,
    )
    assert all(points[i] < points[i + 1] for i in range(len(points) - 1))


# ── _merge_chunk_results (pure function) ─────────────────────────────


def test_merge_chunk_results_offsets_segment_timestamps():
    """Each chunk's segments get their `start`/`end` shifted by the
    chunk's audio offset in the original file."""
    chunk1 = _make_result([
        {"start": 0.0, "end": 10.0, "text": "Привет."},
    ])
    chunk2 = _make_result([
        {"start": 0.0, "end": 15.0, "text": "Как дела?"},
    ])
    merged = _merge_chunk_results(
        results=[chunk1, chunk2], offsets=[0.0, 5400.0],
    )
    assert len(merged.segments) == 2
    assert merged.segments[0]["start"] == 0.0
    assert merged.segments[0]["end"] == 10.0
    assert merged.segments[1]["start"] == 5400.0
    assert merged.segments[1]["end"] == 5415.0


def test_merge_chunk_results_offsets_word_timestamps():
    """Words[] inside segments also get offset — critical for the
    upcoming hybrid path that consumes word-level data."""
    chunk1 = _make_result([
        {
            "start": 0.0, "end": 2.0, "text": "Привет мир.",
            "words": [
                {"start": 0.5, "end": 1.0, "word": "Привет"},
                {"start": 1.2, "end": 1.9, "word": "мир."},
            ],
        },
    ])
    chunk2 = _make_result([
        {
            "start": 0.0, "end": 1.5, "text": "Как дела?",
            "words": [
                {"start": 0.1, "end": 0.4, "word": "Как"},
                {"start": 0.5, "end": 1.4, "word": "дела?"},
            ],
        },
    ])
    merged = _merge_chunk_results(
        results=[chunk1, chunk2], offsets=[0.0, 5400.0],
    )
    # chunk 1 words unchanged (offset=0)
    assert merged.segments[0]["words"][0]["start"] == 0.5
    # chunk 2 words shifted by 5400s
    assert merged.segments[1]["words"][0]["start"] == pytest.approx(5400.1)
    assert merged.segments[1]["words"][1]["end"] == pytest.approx(5401.4)


def test_merge_chunk_results_picks_first_non_none_language():
    """Language tag: pick the first chunk's non-None language. Mixed-mode
    content with auto-detect may yield None on a particular chunk
    (e.g. silence-heavy); shouldn't propagate None upward."""
    c1 = _make_result([], language=None)
    c2 = _make_result([], language="ru")
    c3 = _make_result([], language="en")
    merged = _merge_chunk_results(results=[c1, c2, c3], offsets=[0.0, 100.0, 200.0])
    assert merged.language == "ru"


def test_merge_chunk_results_raw_preserves_per_chunk_payloads():
    """The merged ``raw`` dict carries per-chunk raw payloads for
    debugging — same shape as cloud-only path but enriched with
    chunk metadata."""
    c1 = _make_result([])
    c1.raw = {"chunk_index": 0, "duration": 5400.0}
    c2 = _make_result([])
    c2.raw = {"chunk_index": 1, "duration": 3600.0}
    merged = _merge_chunk_results(results=[c1, c2], offsets=[0.0, 5400.0])
    assert merged.raw is not None
    assert "chunks" in merged.raw
    assert len(merged.raw["chunks"]) == 2


# ── needs_chunking ────────────────────────────────────────────────────


def test_needs_chunking_false_for_small_file(real_wav_tempfile):
    """A 5 MB file with a 25 MB cap doesn't need chunking."""
    p = _StubProvider(results=[])
    with patch("transcriber.cloud_chunker.os.path.getsize", return_value=5 * 1024 * 1024):
        assert needs_chunking(real_wav_tempfile, p) is False


def test_needs_chunking_true_for_oversized_file(real_wav_tempfile):
    """A 60 MB file with a 25 MB cap needs chunking. The chunker should
    not trust opus compression alone to bring it under — even compressed
    it could still be > 25 MB (e.g. 6h recording = ~17 MB at 32 kbps but
    8h recording = ~23 MB). Conservative path: anything 2× the cap
    raw triggers chunking."""
    p = _StubProvider(results=[])
    with patch("transcriber.cloud_chunker.os.path.getsize", return_value=60 * 1024 * 1024):
        assert needs_chunking(real_wav_tempfile, p) is True


def test_needs_chunking_false_when_provider_has_no_cap(real_wav_tempfile):
    """Providers that declare max_upload_bytes=None (Deepgram, etc.)
    never trigger chunking regardless of file size."""
    p = _StubProvider(results=[])
    p.max_upload_bytes = None
    with patch("transcriber.cloud_chunker.os.path.getsize", return_value=500 * 1024 * 1024):
        assert needs_chunking(real_wav_tempfile, p) is False


# ── transcribe_chunked (integration with mocked I/O) ─────────────────


def test_transcribe_chunked_3h_into_two_chunks(real_wav_tempfile):
    """3h audio → 2 chunks at the silence near 90 min. Provider called
    twice; segments stitched with correct offsets; tempfiles cleaned."""
    fake_tmp1 = real_wav_tempfile + ".chunk1.opus"
    fake_tmp2 = real_wav_tempfile + ".chunk2.opus"
    for p in (fake_tmp1, fake_tmp2):
        with open(p, "wb") as f:
            f.write(b"x")

    provider = _StubProvider([
        _make_result([{"start": 0.0, "end": 100.0, "text": "Первая часть."}]),
        _make_result([{"start": 0.0, "end": 50.0, "text": "Вторая часть."}]),
    ])

    extracted_paths: list[str] = []

    def fake_extract(wav_path, start_sec, end_sec):
        path = fake_tmp1 if not extracted_paths else fake_tmp2
        extracted_paths.append(path)
        return path

    with patch("transcriber.cloud_chunker._audio_duration", return_value=3 * 3600.0), \
         patch(
            "transcriber.cloud_chunker._find_silence_boundaries",
            return_value=[(89 * 60.0, 89 * 60.0 + 10)],
         ), \
         patch(
            "transcriber.cloud_chunker._extract_chunk",
            side_effect=fake_extract,
         ), \
         patch(
            "transcriber.cloud_chunker._ensure_wav_for_chunking",
            return_value=(real_wav_tempfile, False),
         ):
        result = transcribe_chunked(
            real_wav_tempfile, provider, TranscriptionOptions(),
        )

    # Provider called once per chunk.
    assert len(provider.calls) == 2
    # First chunk segment unchanged; second chunk's start offset by split point.
    assert len(result.segments) == 2
    assert result.segments[0]["start"] == 0.0
    assert result.segments[1]["start"] == pytest.approx(89 * 60 + 5.0)
    # Tempfiles cleaned.
    assert not os.path.exists(fake_tmp1)
    assert not os.path.exists(fake_tmp2)


def test_transcribe_chunked_cleans_tempfiles_on_provider_error(real_wav_tempfile):
    """If chunk 2 fails mid-stream, chunk 1's tempfile (and 2's, if it
    was extracted before transcribe raised) must still be cleaned."""
    fake_tmp1 = real_wav_tempfile + ".err1.opus"
    fake_tmp2 = real_wav_tempfile + ".err2.opus"
    for p in (fake_tmp1, fake_tmp2):
        with open(p, "wb") as f:
            f.write(b"x")

    class FailingProvider(_StubProvider):
        def transcribe(self, audio_path, options, **kw):
            self.calls.append({"audio_path": audio_path})
            if len(self.calls) == 2:
                raise ProviderError("simulated chunk 2 failure")
            return _make_result([{"start": 0.0, "end": 10.0, "text": "chunk1"}])

    provider = FailingProvider(results=[])
    extracted: list[str] = []

    def fake_extract(wav_path, start_sec, end_sec):
        path = fake_tmp1 if not extracted else fake_tmp2
        extracted.append(path)
        return path

    with patch("transcriber.cloud_chunker._audio_duration", return_value=3 * 3600.0), \
         patch(
            "transcriber.cloud_chunker._find_silence_boundaries",
            return_value=[(89 * 60.0, 89 * 60.0 + 10)],
         ), \
         patch(
            "transcriber.cloud_chunker._extract_chunk",
            side_effect=fake_extract,
         ), \
         patch(
            "transcriber.cloud_chunker._ensure_wav_for_chunking",
            return_value=(real_wav_tempfile, False),
         ):
        with pytest.raises(ProviderError, match="chunk 2 failure"):
            transcribe_chunked(
                real_wav_tempfile, provider, TranscriptionOptions(),
            )

    # Both tempfiles cleaned despite mid-stream failure.
    assert not os.path.exists(fake_tmp1)
    assert not os.path.exists(fake_tmp2)


def test_transcribe_chunked_cancel_mid_chunk_cleans_tempfiles(real_wav_tempfile):
    """User cancels mid-stream → TranscriptionCancelled bubbles. Every
    tempfile the chunker actually extracted gets cleaned, regardless
    of which point in the loop the cancel hit."""
    fake_tmp1 = real_wav_tempfile + ".cancel1.opus"
    # NOTE: tmp2 is NOT pre-created — we want to assert "if the chunker
    # extracted it, it was cleaned", not "any file with this name is
    # cleaned" (the latter would falsely pass when chunker never touched it).
    with open(fake_tmp1, "wb") as f:
        f.write(b"x")

    cancel = threading.Event()
    call_count = [0]

    class CancellingProvider(_StubProvider):
        def transcribe(self, audio_path, options, **kw):
            self.calls.append({"audio_path": audio_path})
            call_count[0] += 1
            if call_count[0] == 1:
                # Trigger cancel after first chunk transcribes ok.
                cancel.set()
                return _make_result([{"start": 0.0, "end": 10.0, "text": "c1"}])
            # Second call should respect cancel_event.
            evt = kw.get("cancel_event")
            if evt is not None and evt.is_set():
                raise TranscriptionCancelled()
            return _make_result([])

    provider = CancellingProvider(results=[])
    extracted: list[str] = []

    def fake_extract(wav_path, start_sec, end_sec):
        # Generate a fresh tempfile on disk per extract — represents
        # what the real _extract_chunk does.
        f = tempfile.NamedTemporaryFile(suffix=".opus", delete=False)
        f.write(b"x")
        f.close()
        extracted.append(f.name)
        return f.name

    with patch("transcriber.cloud_chunker._audio_duration", return_value=3 * 3600.0), \
         patch(
            "transcriber.cloud_chunker._find_silence_boundaries",
            return_value=[(89 * 60.0, 89 * 60.0 + 10)],
         ), \
         patch(
            "transcriber.cloud_chunker._extract_chunk",
            side_effect=fake_extract,
         ), \
         patch(
            "transcriber.cloud_chunker._ensure_wav_for_chunking",
            return_value=(real_wav_tempfile, False),
         ):
        with pytest.raises(TranscriptionCancelled):
            transcribe_chunked(
                real_wav_tempfile, provider, TranscriptionOptions(),
                cancel_event=cancel,
            )

    # Every tempfile the chunker created via _extract_chunk must be
    # gone, regardless of which iteration the cancel hit.
    for path in extracted:
        assert not os.path.exists(path), (
            f"extracted tempfile not cleaned after cancel: {path}"
        )
    # Pre-created fake_tmp1 is NOT one the chunker owned — leave it
    # alone here, the fixture teardown handles it via os.unlink below.
    os.unlink(fake_tmp1)


def test_transcribe_chunked_prompt_continuity(real_wav_tempfile):
    """Chunks after the first receive a prompt= built from the tail of
    the previous chunk's text. This is what OpenAI's Whisper docs
    recommend for boundary accuracy."""
    fake_tmp1 = real_wav_tempfile + ".cont1.opus"
    fake_tmp2 = real_wav_tempfile + ".cont2.opus"
    for p in (fake_tmp1, fake_tmp2):
        with open(p, "wb") as f:
            f.write(b"x")

    long_text = "Это очень длинный фрагмент текста из первого чанка. " * 8
    provider = _StubProvider([
        _make_result([{"start": 0.0, "end": 100.0, "text": long_text}]),
        _make_result([{"start": 0.0, "end": 50.0, "text": "Вторая часть."}]),
    ])

    extracted: list[str] = []

    def fake_extract(wav_path, start_sec, end_sec):
        path = fake_tmp1 if not extracted else fake_tmp2
        extracted.append(path)
        return path

    base_options = TranscriptionOptions(language="ru")

    with patch("transcriber.cloud_chunker._audio_duration", return_value=3 * 3600.0), \
         patch(
            "transcriber.cloud_chunker._find_silence_boundaries",
            return_value=[(89 * 60.0, 89 * 60.0 + 10)],
         ), \
         patch(
            "transcriber.cloud_chunker._extract_chunk",
            side_effect=fake_extract,
         ), \
         patch(
            "transcriber.cloud_chunker._ensure_wav_for_chunking",
            return_value=(real_wav_tempfile, False),
         ):
        transcribe_chunked(real_wav_tempfile, provider, base_options)

    # Chunk 2's options.hotwords carries continuity from chunk 1 tail.
    chunk2_opts = provider.calls[1]["options"]
    # Implementation appends the prefix text to the hotwords list (or uses
    # a dedicated mechanism). Verify it carries SOMETHING derived from
    # the long_text we returned for chunk 1.
    assert chunk2_opts.hotwords, (
        "chunk 2 options must carry prompt continuity from chunk 1"
    )
    # The continuity prompt must contain SOME suffix of chunk 1's text.
    combined = " ".join(chunk2_opts.hotwords)
    assert "первого чанка" in combined or "фрагмент" in combined


def test_transcribe_chunked_progress_fires_per_chunk(real_wav_tempfile):
    """on_progress called at least once per chunk so the UI bar advances."""
    fake_tmp1 = real_wav_tempfile + ".prog1.opus"
    fake_tmp2 = real_wav_tempfile + ".prog2.opus"
    for p in (fake_tmp1, fake_tmp2):
        with open(p, "wb") as f:
            f.write(b"x")

    provider = _StubProvider([
        _make_result([]),
        _make_result([]),
    ])

    extracted: list[str] = []

    def fake_extract(wav_path, start_sec, end_sec):
        path = fake_tmp1 if not extracted else fake_tmp2
        extracted.append(path)
        return path

    progress_values: list[float] = []

    with patch("transcriber.cloud_chunker._audio_duration", return_value=3 * 3600.0), \
         patch(
            "transcriber.cloud_chunker._find_silence_boundaries",
            return_value=[(89 * 60.0, 89 * 60.0 + 10)],
         ), \
         patch(
            "transcriber.cloud_chunker._extract_chunk",
            side_effect=fake_extract,
         ), \
         patch(
            "transcriber.cloud_chunker._ensure_wav_for_chunking",
            return_value=(real_wav_tempfile, False),
         ):
        transcribe_chunked(
            real_wav_tempfile, provider, TranscriptionOptions(),
            on_progress=progress_values.append,
        )

    assert progress_values, "on_progress was never called"
    # Strictly monotonically non-decreasing.
    assert all(
        progress_values[i] <= progress_values[i + 1]
        for i in range(len(progress_values) - 1)
    )
    # Final value reaches 100 (we always end at 100 to close the bar).
    assert progress_values[-1] == pytest.approx(100.0)


def test_transcribe_chunked_status_fires_per_chunk(real_wav_tempfile):
    """on_status called with chunk-index messages so the user knows
    progress is happening (status string is Russian-facing)."""
    fake_tmp1 = real_wav_tempfile + ".stat1.opus"
    fake_tmp2 = real_wav_tempfile + ".stat2.opus"
    for p in (fake_tmp1, fake_tmp2):
        with open(p, "wb") as f:
            f.write(b"x")

    provider = _StubProvider([_make_result([]), _make_result([])])

    extracted: list[str] = []

    def fake_extract(wav_path, start_sec, end_sec):
        path = fake_tmp1 if not extracted else fake_tmp2
        extracted.append(path)
        return path

    status_msgs: list[str] = []

    with patch("transcriber.cloud_chunker._audio_duration", return_value=3 * 3600.0), \
         patch(
            "transcriber.cloud_chunker._find_silence_boundaries",
            return_value=[(89 * 60.0, 89 * 60.0 + 10)],
         ), \
         patch(
            "transcriber.cloud_chunker._extract_chunk",
            side_effect=fake_extract,
         ), \
         patch(
            "transcriber.cloud_chunker._ensure_wav_for_chunking",
            return_value=(real_wav_tempfile, False),
         ):
        transcribe_chunked(
            real_wav_tempfile, provider, TranscriptionOptions(),
            on_status=status_msgs.append,
        )

    # At least one message per chunk + the "merging" / "done" sentinel.
    joined = " | ".join(status_msgs)
    assert "Чанк 1" in joined or "1/2" in joined or "1 из 2" in joined
    assert "Чанк 2" in joined or "2/2" in joined or "2 из 2" in joined
