"""Tests for the torch-free audio I/O helpers.

Avoids the ffmpeg-dependent code paths (ensure_wav with conversion,
split chunking) — those are integration tests that need a real ffmpeg
binary. The branches we cover here exercise pure logic + soundfile.

Two ensure_16khz_mono tests below DO need ffmpeg (the resample path)
and are guarded by ``_FFMPEG_AVAILABLE``; they skip on CI runners
without an ffmpeg install and exercise the real ffmpeg pipeline
locally. The header-only short-circuit path is covered by a separate
ffmpeg-free test.
"""
import os
import shutil

import numpy as np
import pytest
import soundfile as sf

from audio_io import (
    SAMPLE_RATE,
    _ffmpeg_time,
    ensure_16khz_mono,
    ensure_wav,
    get_duration_s,
    load_mono_float32,
    resample_to_16khz_mono,
    split_wav_into_chunks,
)

_FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None

# ── _ffmpeg_time ───────────────────────────────────────────────────


@pytest.mark.parametrize("seconds,expected", [
    (0.0,       "00:00:00.000"),
    (1.5,       "00:00:01.500"),
    (61.0,      "00:01:01.000"),
    (3725.999,  "01:02:05.999"),
])
def test_ffmpeg_time(seconds, expected):
    assert _ffmpeg_time(seconds) == expected


# ── get_duration_s ─────────────────────────────────────────────────


def _write_silent_wav(path, seconds: float, channels: int = 1) -> None:
    """Generate a silent WAV at the project's standard 16 kHz sample rate."""
    n = int(seconds * SAMPLE_RATE)
    if channels > 1:
        data = np.zeros((n, channels), dtype=np.float32)
    else:
        data = np.zeros(n, dtype=np.float32)
    sf.write(str(path), data, SAMPLE_RATE, subtype="PCM_16")


def test_get_duration_matches_source(tmp_path):
    wav = tmp_path / "synthetic.wav"
    _write_silent_wav(wav, 2.5)
    # Sample-quantization rounding can shift duration by a fraction of a
    # frame (1/16000s); a 5 ms tolerance is well below any audible threshold.
    assert get_duration_s(str(wav)) == pytest.approx(2.5, abs=0.005)


# ── ensure_wav short-circuit ───────────────────────────────────────


def test_ensure_wav_returns_input_for_wav_when_not_normalizing(tmp_path):
    """No ffmpeg invocation when input is already WAV and normalize=False."""
    wav = tmp_path / "raw.wav"
    _write_silent_wav(wav, 0.5)
    out_path, is_temp = ensure_wav(str(wav), normalize=False)
    # Same file, no temp copy made.
    assert out_path == str(wav)
    assert is_temp is False


# ── load_mono_float32 ──────────────────────────────────────────────


def test_load_mono_float32_downmixes_stereo(tmp_path):
    wav = tmp_path / "stereo.wav"
    # Distinct per-channel signal so the channel-average is observable.
    n = SAMPLE_RATE  # 1 second
    left = np.full(n, 0.5, dtype=np.float32)
    right = np.full(n, -0.5, dtype=np.float32)
    stereo = np.stack([left, right], axis=1)
    sf.write(str(wav), stereo, SAMPLE_RATE, subtype="PCM_16")

    samples, sr = load_mono_float32(str(wav))
    assert sr == SAMPLE_RATE
    assert samples.ndim == 1
    # Downmix is the per-frame mean: (0.5 + -0.5) / 2 = 0.0 across the file.
    np.testing.assert_allclose(samples, np.zeros(n, dtype=np.float32), atol=1e-3)


# ── split_wav_into_chunks (no-chunk fast path) ─────────────────────


def test_split_returns_single_chunk_when_under_threshold(tmp_path):
    """Files shorter than the chunk size return [(path, 0.0, 0.0)] without copying."""
    wav = tmp_path / "short.wav"
    _write_silent_wav(wav, 1.0)
    out_dir = tmp_path / "chunks"
    chunks = split_wav_into_chunks(str(wav), chunk_duration_s=60, out_dir=str(out_dir))
    assert chunks == [(str(wav), 0.0, 0.0)]
    # No ffmpeg call → no chunks dir created.
    assert not out_dir.exists()


# ── ensure_16khz_mono ─────────────────────────────────────────────


def test_ensure_16khz_mono_short_circuits_for_16k_mono_wav(tmp_path):
    """A WAV that's already 16 kHz mono must be returned as-is,
    is_temp=False (no ffmpeg invocation)."""
    src = tmp_path / "input.wav"
    samples = np.zeros(16_000 * 2, dtype=np.float32)  # 2s of silence
    sf.write(str(src), samples, 16_000, subtype="PCM_16")

    out_path, is_temp = ensure_16khz_mono(str(src))
    assert out_path == str(src)
    assert is_temp is False


@pytest.mark.skipif(
    not _FFMPEG_AVAILABLE,
    reason="ffmpeg binary unavailable (CI runners without ffmpeg skip the resample path)",
)
def test_ensure_16khz_mono_resamples_44100_hz(tmp_path):
    """A 44.1 kHz WAV must be resampled to 16 kHz mono and the resulting
    file written to a temp path. is_temp=True so the caller knows to
    delete."""
    src = tmp_path / "input_44k.wav"
    samples = np.zeros(44_100 * 2, dtype=np.float32)  # 2s of silence at 44.1k
    sf.write(str(src), samples, 44_100, subtype="PCM_16")

    out_path, is_temp = ensure_16khz_mono(str(src))
    try:
        assert is_temp is True
        assert out_path != str(src)
        # Verify the output IS 16 kHz mono
        with sf.SoundFile(out_path) as f:
            assert f.samplerate == 16_000
            assert f.channels == 1
    finally:
        if is_temp:
            try:
                os.unlink(out_path)
            except OSError:
                pass


@pytest.mark.skipif(
    not _FFMPEG_AVAILABLE,
    reason="ffmpeg binary unavailable (CI runners without ffmpeg skip the resample path)",
)
def test_ensure_16khz_mono_resamples_stereo_48k(tmp_path):
    """48 kHz stereo must be both downmixed to mono AND resampled to 16 kHz."""
    src = tmp_path / "input_48k_stereo.wav"
    # 2s of silence, 2 channels
    samples = np.zeros((48_000 * 2, 2), dtype=np.float32)
    sf.write(str(src), samples, 48_000, subtype="PCM_16")

    out_path, is_temp = ensure_16khz_mono(str(src))
    try:
        assert is_temp is True
        with sf.SoundFile(out_path) as f:
            assert f.samplerate == 16_000
            assert f.channels == 1
    finally:
        if is_temp:
            try:
                os.unlink(out_path)
            except OSError:
                pass


# ── resample_to_16khz_mono ────────────────────────────────────────


def test_resample_to_16khz_mono_short_circuits_for_16k_input():
    """16 kHz mono input must be returned as-is (same object, no ffmpeg
    invocation). This is the dominant fast path — most callers already
    pre-resampled upstream, and we want zero overhead in that case."""
    samples = np.zeros(16_000 * 2, dtype=np.float32)  # 2s @ 16k
    result = resample_to_16khz_mono(samples, sample_rate=16_000)
    assert result is samples, "Expected identity return (no copy) for 16k input"


def test_resample_to_16khz_mono_rejects_multi_dim_input():
    """ValueError when input is not 1-D. Catches caller mistakes early
    (e.g. forgetting to channel-average a stereo array before calling)."""
    stereo = np.zeros((44_100 * 2, 2), dtype=np.float32)
    with pytest.raises(ValueError, match="1-D mono"):
        resample_to_16khz_mono(stereo, sample_rate=44_100)


def test_resample_to_16khz_mono_rejects_stereo_at_16k():
    """Regression for Codex finding on PR #35: the ndim check must run
    BEFORE the sample_rate==16k short-circuit, otherwise a stereo 16k
    input silently bypasses the helper and produces a confusing VAD
    error later. Forces fail-fast at the helper boundary."""
    stereo_16k = np.zeros((16_000 * 2, 2), dtype=np.float32)
    with pytest.raises(ValueError, match="1-D mono"):
        resample_to_16khz_mono(stereo_16k, sample_rate=16_000)


@pytest.mark.skipif(
    not _FFMPEG_AVAILABLE,
    reason="ffmpeg binary unavailable (CI runners without ffmpeg skip the resample path)",
)
def test_resample_to_16khz_mono_resamples_44100_to_16000():
    """Real ffmpeg pipe: 1 second of 44.1 kHz samples must come back as
    ~16 000 samples (one second at 16 kHz). Allow ±5 samples of ffmpeg
    resampler edge-effects."""
    src = np.zeros(44_100, dtype=np.float32)  # 1 second @ 44.1k
    result = resample_to_16khz_mono(src, sample_rate=44_100)
    assert result.dtype == np.float32
    assert result.ndim == 1
    assert abs(len(result) - 16_000) <= 5, (
        f"Resampled length {len(result)} not within ±5 of expected 16000"
    )


@pytest.mark.skipif(
    not _FFMPEG_AVAILABLE,
    reason="ffmpeg binary unavailable (CI runners without ffmpeg skip the resample path)",
)
def test_resample_to_16khz_mono_resamples_48000_to_16000():
    """48 kHz → 16 kHz is exact 3:1 downsample. 2 seconds of 48k samples
    must come back as exactly ~32 000 samples (allow ±5 for edge effects)."""
    src = np.zeros(48_000 * 2, dtype=np.float32)  # 2 seconds @ 48k
    result = resample_to_16khz_mono(src, sample_rate=48_000)
    assert result.dtype == np.float32
    assert abs(len(result) - 32_000) <= 5, (
        f"Resampled length {len(result)} not within ±5 of expected 32000"
    )
