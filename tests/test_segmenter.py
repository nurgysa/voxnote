"""Tests for transcriber.segmenter.vad_split — VAD wrapper used by the
Phase 2 mixed-language code path.

Pure module — no Whisper, no GPU. Faster-whisper's Silero VAD is
imported lazily inside vad_split() (matching silence_remover.py's
pattern), so the test process pays the import cost only when these
tests run, not at collection.
"""
from __future__ import annotations

import numpy as np

from transcriber.segmenter import vad_split


def test_vad_split_empty_audio_returns_empty_list():
    """Defensive: a zero-length array must not crash get_speech_timestamps
    and must not produce phantom segments. Matches silence_remover.py's
    empty-input contract."""
    samples = np.array([], dtype=np.float32)
    result = vad_split(samples, sample_rate=16_000)
    assert result == []
