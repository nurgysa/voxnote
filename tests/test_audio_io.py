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
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import numpy as np
import pytest
import soundfile as sf

from audio_io import (
    SAMPLE_RATE,
    _escape_ffmpeg_filter_path,
    _ffmpeg_time,
    _get_rnnoise_model_path,
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


# ── _get_rnnoise_model_path (lazy download) ────────────────────────


def _mock_urlopen_returning(data: bytes) -> MagicMock:
    """Helper: build a urlopen mock that returns the given bytes."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = data
    # Context-manager protocol for the `with urlopen(...) as resp:` pattern.
    mock_resp.__enter__ = lambda self: self
    mock_resp.__exit__ = lambda *a: None
    return mock_resp


def test_get_rnnoise_model_path_downloads_when_missing(tmp_path):
    """First call (cache empty): fetches from GitHub, writes to
    ~/.audio-transcriber/models/rnnoise/, returns the cached path."""
    fake_home = str(tmp_path / "fake_home")
    fake_model_bytes = b"x" * 85_000  # ~85 KB matches real model size

    mock_resp = _mock_urlopen_returning(fake_model_bytes)

    with patch("audio_io.os.path.expanduser", return_value=fake_home), \
         patch("audio_io.urllib.request.urlopen", return_value=mock_resp) as mock_open:
        path = _get_rnnoise_model_path()

    # Cache path is under the patched home dir, in the documented subdir.
    assert path.startswith(fake_home)
    assert "rnnoise" in path
    assert path.endswith(".rnnn")
    # File was actually written with the downloaded bytes.
    with open(path, "rb") as f:
        assert f.read() == fake_model_bytes
    # urlopen called exactly once.
    assert mock_open.call_count == 1


def test_get_rnnoise_model_path_uses_cache_on_second_call(tmp_path):
    """Second call (cache populated): no network roundtrip, returns
    the existing path. This is the hot-path on every transcription
    once the user has enabled denoising once."""
    fake_home = str(tmp_path / "fake_home")
    fake_model_bytes = b"x" * 1024

    mock_resp = _mock_urlopen_returning(fake_model_bytes)

    with patch("audio_io.os.path.expanduser", return_value=fake_home), \
         patch("audio_io.urllib.request.urlopen", return_value=mock_resp) as mock_open:
        first_path = _get_rnnoise_model_path()
        second_path = _get_rnnoise_model_path()

    assert first_path == second_path
    # urlopen called ONCE total, not once per call.
    assert mock_open.call_count == 1


def test_get_rnnoise_model_path_raises_actionable_on_network_error(tmp_path):
    """Network failure → RuntimeError with a Russian message telling
    the user how to recover (disable denoise OR check network).
    No half-written cache file left behind."""
    fake_home = str(tmp_path / "fake_home")

    with patch("audio_io.os.path.expanduser", return_value=fake_home), \
         patch(
            "audio_io.urllib.request.urlopen",
            side_effect=URLError("Network unreachable"),
         ):
        with pytest.raises(RuntimeError, match="RNNoise"):
            _get_rnnoise_model_path()

    # No partial file lingers.
    cache_dir = os.path.join(fake_home, ".audio-transcriber", "models", "rnnoise")
    if os.path.isdir(cache_dir):
        assert not any(
            f.endswith(".rnnn") for f in os.listdir(cache_dir)
        ), "Partial .rnnn file should not be left behind on download failure"


# ── ensure_wav with denoise parameter ─────────────────────────────


def _captured_filter_chain(captured_cmd: list) -> str | None:
    """Pull the -af argument value out of a captured ffmpeg argv list.
    Returns None if no -af was present (e.g. short-circuit path)."""
    try:
        af_idx = captured_cmd.index("-af")
    except ValueError:
        return None
    return captured_cmd[af_idx + 1]


def test_ensure_wav_denoise_false_preserves_existing_filter_chain(tmp_path):
    """Regression guard: denoise=False (the default) produces exactly
    the existing highpass + loudnorm chain. Without this test, a typo
    in the filter-chain construction could silently change every
    transcription's preprocessing — affecting WER on every recording."""
    src = tmp_path / "in.mp3"
    src.write_bytes(b"fake mp3")
    captured = {}

    def fake_run(cmd, capture_output=True, check=True):
        captured["cmd"] = cmd
        # Write a tiny "output" so the caller doesn't blow up trying to
        # read a missing temp file (it doesn't read, but be safe).
        out_idx = -1
        with open(cmd[out_idx], "wb") as f:
            f.write(b"")
        return MagicMock(returncode=0)

    with patch("audio_io.subprocess.run", side_effect=fake_run):
        out_path, _is_temp = ensure_wav(str(src), normalize=True, denoise=False)

    chain = _captured_filter_chain(captured["cmd"])
    assert chain == "highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=11"
    # Cleanup the fake output.
    try:
        os.unlink(out_path)
    except OSError:
        pass


def test_ensure_wav_denoise_true_inserts_arnndn_between_highpass_and_loudnorm(tmp_path):
    """denoise=True: filter chain becomes
    highpass → arnndn=m=<model> → loudnorm. Order matters:
    - highpass first cuts subsonic noise so arnndn doesn't waste cycles
    - arnndn cleans speech-band noise
    - loudnorm runs LAST on the cleaned signal so its measurement
      isn't skewed by the noise floor"""
    src = tmp_path / "in.mp3"
    src.write_bytes(b"fake mp3")
    captured = {}

    def fake_run(cmd, capture_output=True, check=True):
        captured["cmd"] = cmd
        with open(cmd[-1], "wb") as f:
            f.write(b"")
        return MagicMock(returncode=0)

    with patch("audio_io.subprocess.run", side_effect=fake_run), \
         patch(
            "audio_io._get_rnnoise_model_path",
            return_value="/fake/path/sh.rnnn",
         ):
        out_path, _is_temp = ensure_wav(str(src), normalize=True, denoise=True)

    chain = _captured_filter_chain(captured["cmd"])
    assert chain is not None
    # Order: highpass, arnndn, loudnorm.
    parts = chain.split(",")
    assert parts[0] == "highpass=f=80"
    assert parts[1] == "arnndn=m=/fake/path/sh.rnnn"
    assert parts[2] == "loudnorm=I=-16:TP=-1.5:LRA=11"
    try:
        os.unlink(out_path)
    except OSError:
        pass


def test_ensure_wav_denoise_true_normalize_false_omits_loudnorm(tmp_path):
    """User can ask for denoise without loudnorm — e.g. if they
    pre-normalize externally. Chain becomes highpass + arnndn only."""
    src = tmp_path / "in.mp3"
    src.write_bytes(b"fake mp3")
    captured = {}

    def fake_run(cmd, capture_output=True, check=True):
        captured["cmd"] = cmd
        with open(cmd[-1], "wb") as f:
            f.write(b"")
        return MagicMock(returncode=0)

    with patch("audio_io.subprocess.run", side_effect=fake_run), \
         patch(
            "audio_io._get_rnnoise_model_path",
            return_value="/fake/path/sh.rnnn",
         ):
        out_path, _is_temp = ensure_wav(
            str(src), normalize=False, denoise=True,
        )

    chain = _captured_filter_chain(captured["cmd"])
    assert chain == "highpass=f=80,arnndn=m=/fake/path/sh.rnnn"
    try:
        os.unlink(out_path)
    except OSError:
        pass


# ── _escape_ffmpeg_filter_path (Codex P1 fix for PR #56) ──────────


def test_escape_ffmpeg_filter_path_unix_path_is_noop():
    """Unix paths have no backslashes and no colons → escape is a no-op.
    Defends against a future "clever" fix that breaks the Unix case
    while solving Windows."""
    p = "/home/user/.audio-transcriber/models/rnnoise/sh.rnnn"
    assert _escape_ffmpeg_filter_path(p) == p


def test_escape_ffmpeg_filter_path_windows_path_double_escapes_colon():
    """Regression for the SECOND-LEVEL escape bug shipped in PR #57.
    My original "fix" used a single-backslash colon escape (``\\:``);
    that handled only the filter-argument layer. ffmpeg's filtergraph
    parser ALSO does a higher-level escape pass first that consumes one
    backslash. Net effect: real Windows users still saw "No option name
    near '/Users/...'" because by the time the value reached the inner
    parser, ``\\:`` had been peeled to ``:`` and was again treated as a
    separator. Manually reproduced against ffmpeg 6 on Windows before
    fixing.

    The correct escape: ``\\\\:`` (two backslashes + colon in the SOURCE
    string, which Python ``r"\\\\:"`` produces). Outer layer consumes
    one backslash → ``\\:`` reaches inner layer → unescapes to ``:``.

    Cross-platform safe: Unix paths have no backslashes and no colons,
    so the replace passes leave them untouched.
    """
    p = r"C:\Users\nurgisa\.audio-transcriber\models\rnnoise\sh.rnnn"
    escaped = _escape_ffmpeg_filter_path(p)
    # No raw backslashes remain (forward-slash conversion done).
    assert "\\Users" not in escaped
    # Drive-letter colon is double-escaped so it survives BOTH
    # filtergraph-parser passes.
    assert escaped.startswith(r"C\\:")
    # Forward slashes replaced the backslashes.
    assert "/Users/nurgisa/" in escaped


def test_escape_ffmpeg_filter_path_handles_colon_in_middle_of_path():
    """An NTFS alternate data stream path like C:\\foo:bar would have
    two colons; both must be double-escaped. Pathological but defensive."""
    p = r"C:\foo:bar\baz.rnnn"
    escaped = _escape_ffmpeg_filter_path(p)
    # Both colons double-escaped (\\: in raw repr).
    assert escaped.count(r"\\:") == 2
    # No bare unescaped colons remain.
    assert ":" not in escaped.replace(r"\\:", "")


def test_ensure_wav_denoise_true_uses_escaped_windows_path_in_filter(tmp_path):
    """Integration: a Windows-style model path returned by
    _get_rnnoise_model_path is escaped before insertion into the filter
    chain string. Without escaping, this would produce a malformed
    `arnndn=m=C:\\...` filter that ffmpeg rejects with a parse error,
    breaking denoising for every Windows user."""
    src = tmp_path / "in.mp3"
    src.write_bytes(b"fake mp3")
    captured = {}

    def fake_run(cmd, capture_output=True, check=True):
        captured["cmd"] = cmd
        with open(cmd[-1], "wb") as f:
            f.write(b"")
        return MagicMock(returncode=0)

    windows_path = r"C:\Users\nurgisa\.audio-transcriber\models\rnnoise\sh.rnnn"
    with patch("audio_io.subprocess.run", side_effect=fake_run), \
         patch(
            "audio_io._get_rnnoise_model_path",
            return_value=windows_path,
         ):
        out_path, _is_temp = ensure_wav(str(src), normalize=True, denoise=True)

    chain = _captured_filter_chain(captured["cmd"])
    assert chain is not None
    # The raw Windows path with `:` and `\` must NOT appear unescaped
    # anywhere in the filter chain — that's the precise failure mode
    # Codex flagged on PR #56 (and that my "fix" PR #57 still triggered
    # because of the second-level escape — see
    # test_escape_ffmpeg_filter_path_windows_path_double_escapes_colon).
    assert "arnndn=m=C:\\" not in chain
    # The DOUBLE-escaped form must appear: `arnndn=m=C\\:/Users/...`
    # (raw repr shows two backslashes — needed to survive ffmpeg's
    # two-level filtergraph parser).
    assert r"arnndn=m=C\\:/Users/nurgisa/" in chain
    try:
        os.unlink(out_path)
    except OSError:
        pass


# ── Real-ffmpeg integration tests (the safety net we should have ──
# ── had on PR #57 — mocks verify string composition, only a real    ──
# ── ffmpeg verifies that string is PARSEABLE) ────────────────────────


@pytest.mark.skipif(not _FFMPEG_AVAILABLE, reason="ffmpeg not on PATH")
def test_ensure_wav_with_denoise_runs_real_ffmpeg(tmp_path):
    """Run the FULL denoise path through real ffmpeg and assert it
    doesn't crash. Mocked tests can verify the filter chain we BUILD,
    but only a real ffmpeg can verify that string is PARSEABLE.

    Background: PR #56 introduced the arnndn filter, PR #57 "fixed"
    Windows escape but used single-backslash which still failed in
    production. Both PRs had passing mocked tests. This is the test
    that would have caught both bugs in CI on Day 0.

    Requires the RNNoise model to already be cached locally. We don't
    download in tests (offline-safe principle); if absent, skip with
    a hint so a developer running locally can warm the cache once.
    """
    cached_model = os.path.join(
        os.path.expanduser("~"),
        ".audio-transcriber", "models", "rnnoise", "sh.rnnn",
    )
    if not os.path.isfile(cached_model):
        pytest.skip(
            f"RNNoise model not cached at {cached_model}. Enable "
            f"'Подавлять шум' in the app once to download it, then "
            f"re-run this test."
        )

    src = tmp_path / "real_input.wav"
    _write_silent_wav(src, 1.0)

    with patch("audio_io._get_rnnoise_model_path", return_value=cached_model):
        # If the filter chain is malformed, ensure_wav raises
        # RuntimeError with the ffmpeg stderr — test fails loudly
        # exactly the way the user saw it crash in production.
        out_path, is_temp = ensure_wav(
            str(src), normalize=True, denoise=True,
        )

    # ffmpeg accepted the filter chain and produced a WAV.
    assert os.path.isfile(out_path)
    if is_temp:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def test_ensure_wav_wav_input_normalize_false_denoise_true_runs_ffmpeg(tmp_path):
    """The short-circuit (return input as-is) only fires when BOTH
    normalize=False AND denoise=False. denoise=True alone must still
    trigger an ffmpeg pass — even for a .wav input — otherwise the
    user-requested denoising silently won't happen."""
    src = tmp_path / "raw.wav"
    _write_silent_wav(src, 0.5)
    captured = {}

    def fake_run(cmd, capture_output=True, check=True):
        captured["cmd"] = cmd
        with open(cmd[-1], "wb") as f:
            f.write(b"")
        return MagicMock(returncode=0)

    with patch("audio_io.subprocess.run", side_effect=fake_run), \
         patch(
            "audio_io._get_rnnoise_model_path",
            return_value="/fake/path/sh.rnnn",
         ):
        out_path, is_temp = ensure_wav(
            str(src), normalize=False, denoise=True,
        )

    # ffmpeg WAS invoked (not the short-circuit) — captured["cmd"] populated.
    assert "cmd" in captured, "ffmpeg should have been called for denoise=True"
    chain = _captured_filter_chain(captured["cmd"])
    assert chain == "highpass=f=80,arnndn=m=/fake/path/sh.rnnn"
    # And the returned path is a NEW temp file, not the input.
    assert out_path != str(src)
    assert is_temp is True
    try:
        os.unlink(out_path)
    except OSError:
        pass
