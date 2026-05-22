"""Tests for the language='mixed' path in Transcriber.transcribe().

Mock-based — no real Whisper model, no GPU. Stubs:
  - WhisperModel via MagicMock on the Transcriber._model attribute
  - faster_whisper.vad's get_speech_timestamps via patching segmenter.vad_split
  - audio loading via patching audio_io.load_mono_float32
  - ensure_wav / diarize subprocess via patching at the import site
"""
from __future__ import annotations

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
