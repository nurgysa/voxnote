"""Tests for the language='mixed' path in Transcriber.transcribe().

Mock-based — no real Whisper model, no GPU. Stubs:
  - WhisperModel via MagicMock on the Transcriber._model attribute
  - faster_whisper.vad's get_speech_timestamps via patching segmenter.vad_split
  - audio loading via patching audio_io.load_mono_float32
  - ensure_wav / diarize subprocess via patching at the import site
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from transcriber import Transcriber, _assign_speakers_word_level


def _make_fake_model(per_call_results):
    """Build a MagicMock that mimics faster_whisper.WhisperModel.

    ``per_call_results`` is a list of (segments_iter, info) tuples; each
    successive ``model.transcribe()`` invocation pops one and returns it.
    """
    model = MagicMock()
    model.model = MagicMock()  # for unload_model() / load_model() during offload
    calls = iter(per_call_results)

    def fake_transcribe(audio, **kwargs):
        return next(calls)

    model.transcribe.side_effect = fake_transcribe
    return model


def _make_segment(start, end, text, words=None):
    """Build a faster_whisper segment stand-in (duck-typed)."""
    seg = MagicMock()
    seg.start = start
    seg.end = end
    seg.text = text
    seg.words = words
    return seg


def _make_info(language="ru"):
    info = MagicMock()
    info.language = language
    return info


def test_mixed_routes_to_per_segment_path():
    """When language='mixed', the chunk-loop dispatches to the VAD-pre-pass
    branch and model.transcribe() is called once PER VAD segment, not
    once per chunk."""
    t = Transcriber(model_size="tiny")  # size irrelevant — model is mocked
    # Three VAD segments → three transcribe() calls.
    t._model = _make_fake_model([
        (iter([_make_segment(0.0, 1.0, "Сәлеметсіз бе")]), _make_info("kk")),
        (iter([_make_segment(0.0, 2.0, "Окей, давайте")]), _make_info("ru")),
        (iter([_make_segment(0.0, 1.5, "Slack deployment")]), _make_info("en")),
    ])

    fake_samples = np.zeros(16_000 * 30, dtype=np.float32)  # 30s of "audio"
    vad_segments = [
        {"start": 0, "end": 16_000 * 5},
        {"start": 16_000 * 10, "end": 16_000 * 20},
        {"start": 16_000 * 22, "end": 16_000 * 28},
    ]

    with patch("transcriber.load_mono_float32", return_value=(fake_samples, 16_000)), \
         patch("transcriber.vad_split", return_value=vad_segments):
        out = t._decode_chunk_mixed(
            chunk_path="fake.wav",
            chunk_start_abs=0.0,
            primary_start_abs=0.0,
            initial_prompt="trilingual frame",
            hotwords_str=None,
            cancel_event=None,
        )

    # One model.transcribe call per VAD segment.
    assert t._model.transcribe.call_count == 3
    # Three transcript segments out (one per call's single Whisper segment).
    assert len(out) == 3
    # Texts preserved.
    assert [s["text"] for s in out] == [
        "Сәлеметсіз бе", "Окей, давайте", "Slack deployment",
    ]


def test_mixed_last_segments_carry_language_end_to_end():
    """The transcribe() public path with diarize=False must surface
    info.language into Transcriber.last_segments[].language. This is
    the spec contract for SRT/VTT exporters and future features.

    Regression guard against the no-diarize projection at
    transcriber/__init__.py stripping the field.
    """
    t = Transcriber(model_size="tiny")
    t._model = _make_fake_model([
        (iter([_make_segment(0.0, 1.0, "Привет")]), _make_info("ru")),
        (iter([_make_segment(0.0, 1.0, "Hello")]), _make_info("en")),
    ])
    fake_samples = np.zeros(16_000 * 20, dtype=np.float32)
    vad_segments = [
        {"start": 0, "end": 16_000 * 3},
        {"start": 16_000 * 5, "end": 16_000 * 8},
    ]

    with patch("transcriber.ensure_wav", return_value=("fake.wav", False)), \
         patch("transcriber.get_duration_s", return_value=20.0), \
         patch("transcriber.split_wav_into_chunks", return_value=[("fake.wav", 0.0, 0.0)]), \
         patch("transcriber.load_mono_float32", return_value=(fake_samples, 16_000)), \
         patch("transcriber.vad_split", return_value=vad_segments):
        t.transcribe(
            audio_path="fake.wav",
            language="mixed",
            diarize=False,
        )

    assert len(t.last_segments) == 2
    assert [s.get("language") for s in t.last_segments] == ["ru", "en"]
    # Single-mode contract: text+start+end fields still present
    for s in t.last_segments:
        assert "text" in s
        assert "start" in s
        assert "end" in s


def test_mixed_language_survives_speaker_alignment():
    """speaker_aligner._assign_speakers_word_level must preserve the
    'language' field on input segments. Without this, the diarize=True
    path would strip the metadata before it reaches last_segments.

    Covers both branches of _assign_speakers_word_level:
      - words-present branch (flows through _flush_word_group)
      - empty-words fallback (flows through _find_speaker_by_overlap)
    """
    # Branch 1: segment with words → split along speaker turns via
    # _flush_word_group. Use two words on different speakers to force
    # at least two emitted sub-segments — exercises the language-
    # propagation path through _flush_word_group's parameter.
    segments = [{
        "start": 0.0,
        "end": 2.0,
        "text": "Привет мир",
        "words": [
            {"start": 0.0, "end": 0.8, "word": "Привет"},
            {"start": 1.2, "end": 2.0, "word": " мир"},
        ],
        "language": "ru",
    }]
    speaker_turns = [(0.0, 1.0, "SPEAKER_00"), (1.0, 2.0, "SPEAKER_01")]
    out = _assign_speakers_word_level(segments, speaker_turns)
    assert len(out) == 2
    assert all(seg.get("language") == "ru" for seg in out)

    # Branch 2: segment without words → falls through to
    # _find_speaker_by_overlap fallback. Same language-propagation
    # contract; different code path.
    segments_no_words = [{
        "start": 0.0,
        "end": 2.0,
        "text": "Hello world",
        "words": [],
        "language": "en",
    }]
    fallback_turns = [(0.0, 2.0, "SPEAKER_00")]
    out2 = _assign_speakers_word_level(segments_no_words, fallback_turns)
    assert len(out2) == 1
    assert out2[0].get("language") == "en"

    # Single-mode contract: when input has NO 'language' key, output
    # MUST NOT inject 'language': None — dict shape stays byte-
    # identical to pre-Phase-2 for the dominant runtime path.
    single_mode = [{
        "start": 0.0,
        "end": 1.0,
        "text": "Hi",
        "words": [{"start": 0.0, "end": 1.0, "word": "Hi"}],
    }]
    out3 = _assign_speakers_word_level(single_mode, [(0.0, 1.0, "SPEAKER_00")])
    assert "language" not in out3[0]
    # Same single-mode contract on the fallback branch.
    single_mode_no_words = [{
        "start": 0.0, "end": 1.0, "text": "Hi", "words": [],
    }]
    out4 = _assign_speakers_word_level(
        single_mode_no_words, [(0.0, 1.0, "SPEAKER_00")],
    )
    assert "language" not in out4[0]


def test_mixed_passes_language_none_and_vad_filter_false():
    """Critical: each per-segment transcribe call must pass language=None
    (so Whisper auto-detects this slice's language) and vad_filter=False
    (we already filtered upstream)."""
    t = Transcriber(model_size="tiny")
    t._model = _make_fake_model([
        (iter([_make_segment(0.0, 1.0, "x")]), _make_info("kk")),
        (iter([_make_segment(0.0, 1.0, "y")]), _make_info("ru")),
    ])
    fake_samples = np.zeros(16_000 * 10, dtype=np.float32)
    vad_segments = [
        {"start": 0, "end": 16_000 * 3},
        {"start": 16_000 * 5, "end": 16_000 * 8},
    ]

    with patch("transcriber.load_mono_float32", return_value=(fake_samples, 16_000)), \
         patch("transcriber.vad_split", return_value=vad_segments):
        t._decode_chunk_mixed(
            chunk_path="fake.wav",
            chunk_start_abs=0.0,
            primary_start_abs=0.0,
            initial_prompt="frame",
            hotwords_str=None,
            cancel_event=None,
        )

    # Every transcribe() call must have language=None + vad_filter=False.
    for call in t._model.transcribe.call_args_list:
        kwargs = call.kwargs
        assert kwargs["language"] is None, f"Expected language=None, got {kwargs.get('language')!r}"
        assert kwargs["vad_filter"] is False, (
            f"Expected vad_filter=False, got {kwargs.get('vad_filter')!r}"
        )


def test_mixed_passes_trilingual_prompt_through():
    """The initial_prompt passed to _decode_chunk_mixed must reach every
    per-segment transcribe call verbatim. In real usage this is the
    trilingual frame from _build_initial_prompt('mixed', ...)."""
    t = Transcriber(model_size="tiny")
    t._model = _make_fake_model([
        (iter([_make_segment(0.0, 1.0, "x")]), _make_info("ru")),
        (iter([_make_segment(0.0, 1.0, "y")]), _make_info("kk")),
    ])
    fake_samples = np.zeros(16_000 * 10, dtype=np.float32)
    vad_segments = [
        {"start": 0, "end": 16_000 * 3},
        {"start": 16_000 * 5, "end": 16_000 * 8},
    ]

    expected_prompt = "Расшифровка трилингвальной речи..."

    with patch("transcriber.load_mono_float32", return_value=(fake_samples, 16_000)), \
         patch("transcriber.vad_split", return_value=vad_segments):
        t._decode_chunk_mixed(
            chunk_path="fake.wav",
            chunk_start_abs=0.0,
            primary_start_abs=0.0,
            initial_prompt=expected_prompt,
            hotwords_str=None,
            cancel_event=None,
        )

    for call in t._model.transcribe.call_args_list:
        assert call.kwargs["initial_prompt"] == expected_prompt


def test_mixed_output_segments_carry_language_field():
    """Each output transcript dict must include a 'language' key set
    from info.language. This is the metadata downstream consumers
    (SRT/VTT export, future features) read."""
    t = Transcriber(model_size="tiny")
    t._model = _make_fake_model([
        (iter([_make_segment(0.0, 1.0, "kz text")]), _make_info("kk")),
        (iter([_make_segment(0.0, 1.0, "ru text")]), _make_info("ru")),
        (iter([_make_segment(0.0, 1.0, "en text")]), _make_info("en")),
    ])
    fake_samples = np.zeros(16_000 * 15, dtype=np.float32)
    vad_segments = [
        {"start": 0, "end": 16_000 * 3},
        {"start": 16_000 * 4, "end": 16_000 * 7},
        {"start": 16_000 * 9, "end": 16_000 * 12},
    ]

    with patch("transcriber.load_mono_float32", return_value=(fake_samples, 16_000)), \
         patch("transcriber.vad_split", return_value=vad_segments):
        out = t._decode_chunk_mixed(
            chunk_path="fake.wav",
            chunk_start_abs=0.0,
            primary_start_abs=0.0,
            initial_prompt="frame",
            hotwords_str=None,
            cancel_event=None,
        )

    assert [s["language"] for s in out] == ["kk", "ru", "en"]


def test_mixed_segment_timestamps_offset_correctly():
    """A Whisper-emitted segment at local time t inside VAD slice
    starting at seg_start_s inside chunk starting at chunk_start_abs
    must produce abs_start = chunk_start_abs + seg_start_s + t."""
    t = Transcriber(model_size="tiny")
    t._model = _make_fake_model([
        # Whisper sees a 5-second slice and emits a segment from 1.0 to 3.5 within it.
        (iter([_make_segment(1.0, 3.5, "in slice", words=None)]), _make_info("ru")),
    ])
    fake_samples = np.zeros(16_000 * 60, dtype=np.float32)
    # VAD says slice runs from 10s to 15s within the chunk.
    vad_segments = [{"start": 16_000 * 10, "end": 16_000 * 15}]

    with patch("transcriber.load_mono_float32", return_value=(fake_samples, 16_000)), \
         patch("transcriber.vad_split", return_value=vad_segments):
        out = t._decode_chunk_mixed(
            chunk_path="fake.wav",
            chunk_start_abs=900.0,   # chunk starts at 15-min mark in original file
            primary_start_abs=900.0,
            initial_prompt="frame",
            hotwords_str=None,
            cancel_event=None,
        )

    assert len(out) == 1
    seg = out[0]
    # abs_start = 900 (chunk) + 10 (vad slice start in chunk) + 1.0 (whisper local) = 911.0
    assert seg["start"] == pytest.approx(911.0, abs=0.01)
    # abs_end = 900 + 10 + 3.5 = 913.5
    assert seg["end"] == pytest.approx(913.5, abs=0.01)


def test_mixed_empty_vad_yields_empty_transcript():
    """If vad_split returns [], _decode_chunk_mixed returns [] without
    calling model.transcribe at all. Important for chunks that are
    entirely silent — they should contribute nothing, not crash."""
    t = Transcriber(model_size="tiny")
    t._model = MagicMock()

    fake_samples = np.zeros(16_000 * 10, dtype=np.float32)
    with patch("transcriber.load_mono_float32", return_value=(fake_samples, 16_000)), \
         patch("transcriber.vad_split", return_value=[]):
        out = t._decode_chunk_mixed(
            chunk_path="silent.wav",
            chunk_start_abs=0.0,
            primary_start_abs=0.0,
            initial_prompt="frame",
            hotwords_str=None,
            cancel_event=None,
        )

    assert out == []
    assert t._model.transcribe.call_count == 0


def test_mixed_dedup_drops_segments_before_primary_start():
    """For overlap chunks (chunk_start_abs < primary_start_abs), the same
    midpoint-based dedup as _decode_chunk_single must apply: segments
    whose midpoint is before primary_start_abs are dropped."""
    t = Transcriber(model_size="tiny")
    t._model = _make_fake_model([
        # First VAD slice contributes a segment whose absolute midpoint
        # is BEFORE primary_start_abs — should be dropped.
        # Second VAD slice produces a segment past primary_start_abs — kept.
        (iter([_make_segment(0.0, 1.0, "dropped")]), _make_info("ru")),
        (iter([_make_segment(0.0, 1.0, "kept")]), _make_info("ru")),
    ])
    fake_samples = np.zeros(16_000 * 30, dtype=np.float32)
    vad_segments = [
        {"start": 0,            "end": 16_000 * 2},    # 0-2s in chunk
        {"start": 16_000 * 5,   "end": 16_000 * 8},    # 5-8s in chunk
    ]

    with patch("transcriber.load_mono_float32", return_value=(fake_samples, 16_000)), \
         patch("transcriber.vad_split", return_value=vad_segments):
        out = t._decode_chunk_mixed(
            chunk_path="fake.wav",
            chunk_start_abs=100.0,
            primary_start_abs=103.0,   # primary starts 3s into this chunk
            initial_prompt="frame",
            hotwords_str=None,
            cancel_event=None,
        )

    # Slice 1 segment midpoint = 100 + 0 + 0.5 = 100.5 < 103 → DROPPED
    # Slice 2 segment midpoint = 100 + 5 + 0.5 = 105.5 ≥ 103 → KEPT
    assert [s["text"] for s in out] == ["kept"]


def test_mixed_cancel_event_breaks_inner_loop():
    """Setting cancel_event mid-loop must raise TranscriptionCancelled
    on the next _check_cancelled, before processing more segments."""
    from transcriber import TranscriptionCancelled

    cancel = threading.Event()
    t = Transcriber(model_size="tiny")

    call_count = {"n": 0}

    def fake_transcribe(audio, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            cancel.set()  # cancel after second segment is being processed
        return (iter([_make_segment(0.0, 1.0, f"seg{call_count['n']}")]), _make_info("ru"))

    t._model = MagicMock()
    t._model.transcribe.side_effect = fake_transcribe

    fake_samples = np.zeros(16_000 * 30, dtype=np.float32)
    vad_segments = [
        {"start": 0,            "end": 16_000 * 3},
        {"start": 16_000 * 5,   "end": 16_000 * 8},
        {"start": 16_000 * 10,  "end": 16_000 * 13},
    ]

    with patch("transcriber.load_mono_float32", return_value=(fake_samples, 16_000)), \
         patch("transcriber.vad_split", return_value=vad_segments):
        with pytest.raises(TranscriptionCancelled):
            t._decode_chunk_mixed(
                chunk_path="fake.wav",
                chunk_start_abs=0.0,
                primary_start_abs=0.0,
                initial_prompt="frame",
                hotwords_str=None,
                cancel_event=cancel,
            )

    # Should have called transcribe at most 2 times (one before cancel,
    # one during which cancel was set). The third VAD segment must NOT
    # have been processed.
    assert t._model.transcribe.call_count <= 2


def test_mixed_word_timestamps_offset_correctly():
    """When Whisper emits per-word timestamps within a VAD slice, the
    word abs times must include both chunk_start_abs AND seg_start_s.
    Diarization downstream (speaker_aligner) indexes by word times, so
    a missing offset would mis-align speakers."""
    t = Transcriber(model_size="tiny")
    fake_word = MagicMock()
    fake_word.start = 0.5
    fake_word.end = 1.0
    fake_word.word = "Hello"

    t._model = _make_fake_model([
        (iter([_make_segment(0.0, 1.0, "Hello", words=[fake_word])]), _make_info("en")),
    ])
    fake_samples = np.zeros(16_000 * 30, dtype=np.float32)
    vad_segments = [{"start": 16_000 * 10, "end": 16_000 * 15}]

    with patch("transcriber.load_mono_float32", return_value=(fake_samples, 16_000)), \
         patch("transcriber.vad_split", return_value=vad_segments):
        out = t._decode_chunk_mixed(
            chunk_path="fake.wav",
            chunk_start_abs=600.0,    # chunk starts at 10-min mark
            primary_start_abs=600.0,
            initial_prompt="frame",
            hotwords_str=None,
            cancel_event=None,
        )

    assert len(out) == 1
    words = out[0]["words"]
    assert len(words) == 1
    # word abs_start = 600 (chunk) + 10 (vad slice start) + 0.5 (whisper local) = 610.5
    assert words[0]["start"] == pytest.approx(610.5, abs=0.01)
    assert words[0]["end"] == pytest.approx(611.0, abs=0.01)
