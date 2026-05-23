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


def _speech_like(n_samples: int, *, seed: int, sr: int = 16_000) -> np.ndarray:
    """Build a synthetic speech-like signal that triggers Silero VAD.

    Plain ``rng.standard_normal`` white noise — even loud — does not cross
    Silero's neural-network speech threshold at any amplitude because
    Silero is trained to recognise periodic glottal pulses + formant
    structure, not flat-spectrum noise. We approximate a vowel here:
    a harmonic stack at f0=150 Hz weighted by Lorentzian resonances
    around F1=730, F2=1090, F3=2440 (typical /a/ formants), with a
    syllable-rate amplitude envelope. The rng-seeded phase per harmonic
    keeps the per-test variability the plan calls for while making the
    signal actually look like speech to Silero.
    """
    rng = np.random.default_rng(seed=seed)
    t = np.arange(n_samples) / sr
    f0 = 150.0
    signal = np.zeros(n_samples, dtype=np.float32)
    for h in range(1, 30):
        f = f0 * h
        gain = 0.0
        for fc, q in ((730.0, 100.0), (1090.0, 100.0), (2440.0, 100.0)):
            gain += 1.0 / (1.0 + ((f - fc) / q) ** 2)
        phase = rng.uniform(0.0, 2 * np.pi)
        signal += (gain * np.sin(2 * np.pi * f * t + phase)).astype(np.float32)
    # Amplitude modulation at 8 Hz. Silero needs envelope variation to
    # classify as speech, but at the 4 Hz syllable rate the modulation
    # troughs are wide enough (~125 ms) for Silero to read them as
    # internal silence — breaking the micro-blip merge assertion. 8 Hz
    # keeps troughs under ~60 ms so they stay below Silero's
    # min_silence accounting while still looking speech-like overall.
    env = (0.5 + 0.4 * np.sin(2 * np.pi * 8.0 * t)).astype(np.float32)
    signal = signal * env
    signal = (signal / max(float(np.abs(signal).max()), 1e-8) * 0.5).clip(-1.0, 1.0)
    return signal.astype(np.float32)


def test_vad_split_empty_audio_returns_empty_list():
    """Defensive: a zero-length array must not crash get_speech_timestamps
    and must not produce phantom segments. Matches silence_remover.py's
    empty-input contract."""
    samples = np.array([], dtype=np.float32)
    result = vad_split(samples, sample_rate=16_000)
    assert result == []


def test_vad_split_all_silence_returns_empty_list():
    """5 seconds of literal zeros must not yield any speech regions.
    Silero VAD's threshold defaults are well above the noise floor, so
    a zero-amplitude signal should never cross it."""
    samples = np.zeros(16_000 * 5, dtype=np.float32)
    result = vad_split(samples, sample_rate=16_000)
    assert result == []


def test_vad_split_all_speech_returns_one_group():
    """Synthetic speech-like signal across the entire input should yield
    a single speech region spanning most of the input. Exact boundaries
    depend on VAD's internal frame alignment; we assert structural
    properties, not byte-exact start/end."""
    samples = _speech_like(16_000 * 5, seed=42)
    result = vad_split(samples, sample_rate=16_000)
    assert len(result) == 1, f"Expected 1 group, got {len(result)}: {result}"
    # Should cover most of the input (allow some VAD frame padding).
    seg = result[0]
    assert seg["start"] < 16_000  # starts within first second
    assert seg["end"] > 16_000 * 4  # ends past 4 seconds


def test_vad_split_alternating_returns_two_groups():
    """Speech-silence-speech pattern (each block 2s) must yield exactly
    two speech regions with a gap between them. Tests that VAD's
    min_silence_duration_ms=500 doesn't merge regions separated by
    longer silence."""
    block_samples = 16_000 * 2  # 2 seconds at 16 kHz
    speech1 = _speech_like(block_samples, seed=7)
    silence = np.zeros(block_samples, dtype=np.float32)
    speech2 = _speech_like(block_samples, seed=8)
    samples = np.concatenate([speech1, silence, speech2])

    result = vad_split(samples, sample_rate=16_000)
    assert len(result) == 2, f"Expected 2 groups, got {len(result)}: {result}"
    # First group entirely within first 2 seconds (allow small padding).
    assert result[0]["end"] <= block_samples + 16_000 // 2  # +0.5s padding tolerance
    # Second group starts after the silence block.
    assert result[1]["start"] >= block_samples * 2 - 16_000 // 2


def test_vad_split_micro_blips_merged():
    """Speech blocks separated by silence shorter than
    min_silence_duration_ms=500 must be MERGED into a single region.
    Validates that we picked the right min_silence param for Phase 2's
    language-detection use case."""
    block_samples = 16_000 * 2  # 2s speech
    micro_silence = np.zeros(int(16_000 * 0.1), dtype=np.float32)  # 100ms — < 500ms
    speech1 = _speech_like(block_samples, seed=13)
    speech2 = _speech_like(block_samples, seed=14)
    samples = np.concatenate([speech1, micro_silence, speech2])

    result = vad_split(samples, sample_rate=16_000)
    # Single region because the micro-silence is below the min_silence threshold.
    assert len(result) == 1, f"Expected 1 group (micro-silence merged), got {len(result)}: {result}"


def test_vad_split_forwards_sampling_rate_to_vad():
    """vad_split must pass its sample_rate parameter through to
    get_speech_timestamps as the `sampling_rate` kwarg. This keeps the
    ms→samples threshold conversions inside faster-whisper aligned with
    the input's wall-time rate.

    Test strategy: monkeypatch get_speech_timestamps and assert the
    kwarg is forwarded. We don't assert on actual Silero behavior at
    non-16k because Silero's model is 16k-only and faster-whisper
    doesn't resample — that limitation is documented in the source's
    NOTE block and out of scope for this regression test.
    """
    from unittest.mock import patch

    samples = np.zeros(44_100, dtype=np.float32)  # 1 second @ 44.1k
    captured_kwargs: dict = {}

    def fake_get_speech_timestamps(audio, vad_options=None, **kwargs):
        captured_kwargs.update(kwargs)
        return []

    # Lazy import inside vad_split — patch at the source module.
    with patch(
        "faster_whisper.vad.get_speech_timestamps",
        side_effect=fake_get_speech_timestamps,
    ):
        vad_split(samples, sample_rate=44_100)

    assert captured_kwargs.get("sampling_rate") == 44_100, (
        f"sample_rate not forwarded to VAD. Got kwargs: {captured_kwargs}"
    )
