"""Tests for the ffmpeg-only free-tier upload-cap preparation helpers.

No real ffmpeg: subprocess.run is monkeypatched throughout, mirroring the
pattern in tests/test_audio_io.py. These are pure/fast unit tests — no
network, no real audio decoding.
"""
from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock

import pytest

import audio_upload_prep as prep

# ── target_bytes_for_cap ──────────────────────────────────────────

def test_target_bytes_for_cap_applies_safety_margin():
    cap = 25 * 1024 * 1024
    assert prep.target_bytes_for_cap(cap) == int(cap * 0.92)


# ── _bitrate_for_target ───────────────────────────────────────────

def test_bitrate_for_target_typical_long_meeting():
    # 62.7 min meeting, 25 MiB cap with the standard safety margin.
    target_bytes = prep.target_bytes_for_cap(25 * 1024 * 1024)
    bitrate = prep._bitrate_for_target(3762.0, target_bytes)
    assert bitrate == 51286


def test_bitrate_for_target_returns_none_below_floor():
    # 3 hour meeting: even the floor bitrate can't fit under the cap.
    target_bytes = prep.target_bytes_for_cap(25 * 1024 * 1024)
    assert prep._bitrate_for_target(10800.0, target_bytes) is None


def test_bitrate_for_target_clamped_to_max_for_short_audio():
    # Tiny duration -> uncapped ideal bitrate must clamp to the max.
    assert prep._bitrate_for_target(1.0, 25 * 1024 * 1024) == prep._MAX_BITRATE_BPS


def test_bitrate_for_target_zero_duration_returns_max():
    assert prep._bitrate_for_target(0.0, 1000) == prep._MAX_BITRATE_BPS


# ── compress_for_size_cap ──────────────────────────────────────────

def _fake_run_writes_output(cmd, capture_output=True, check=True):
    with open(cmd[-1], "wb") as f:
        f.write(b"\x00" * 10)
    return MagicMock(returncode=0)


def test_compress_for_size_cap_invokes_ffmpeg_with_computed_bitrate(monkeypatch):
    captured = {}

    def fake_run(cmd, capture_output=True, check=True):
        captured["cmd"] = cmd
        return _fake_run_writes_output(cmd, capture_output, check)

    monkeypatch.setattr(prep, "_require_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(prep.subprocess, "run", fake_run)

    target_bytes = prep.target_bytes_for_cap(25 * 1024 * 1024)
    result = prep.compress_for_size_cap("in.wav", 3762.0, target_bytes)

    assert result is not None
    out_path, is_temp = result
    assert is_temp is True
    assert os.path.isfile(out_path)
    os.unlink(out_path)

    cmd = captured["cmd"]
    assert cmd[0] == "ffmpeg"
    assert "-i" in cmd and cmd[cmd.index("-i") + 1] == "in.wav"
    assert "-ac" in cmd and cmd[cmd.index("-ac") + 1] == "1"
    assert "-ar" in cmd and cmd[cmd.index("-ar") + 1] == "16000"
    assert "-b:a" in cmd and cmd[cmd.index("-b:a") + 1] == "51k"
    assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "libmp3lame"
    assert "-af" in cmd and cmd[cmd.index("-af") + 1] == prep._LOUDNORM_FILTER


def test_compress_for_size_cap_returns_none_without_calling_ffmpeg_when_below_floor(monkeypatch):
    monkeypatch.setattr(prep, "_require_ffmpeg", lambda: (_ for _ in ()).throw(
        AssertionError("ffmpeg must not be invoked when bitrate is below the floor")
    ))

    target_bytes = prep.target_bytes_for_cap(25 * 1024 * 1024)
    result = prep.compress_for_size_cap("in.wav", 10800.0, target_bytes)

    assert result is None


def test_compress_for_size_cap_raises_and_cleans_temp_on_ffmpeg_failure(monkeypatch):
    monkeypatch.setattr(prep, "_require_ffmpeg", lambda: "ffmpeg")

    def fake_run(cmd, capture_output=True, check=True):
        raise subprocess.CalledProcessError(1, cmd, stderr=b"boom")

    monkeypatch.setattr(prep.subprocess, "run", fake_run)

    seen_paths = []
    real_tempfile_ctor = prep.tempfile.NamedTemporaryFile

    def spying_ctor(*a, **k):
        tmp = real_tempfile_ctor(*a, **k)
        seen_paths.append(tmp.name)
        return tmp

    monkeypatch.setattr(prep.tempfile, "NamedTemporaryFile", spying_ctor)

    target_bytes = prep.target_bytes_for_cap(25 * 1024 * 1024)
    with pytest.raises(prep.AudioPrepError, match="Groq"):
        prep.compress_for_size_cap("in.wav", 3762.0, target_bytes)

    assert seen_paths, "expected a temp file to have been created"
    assert not os.path.exists(seen_paths[0]), "failed temp output must be cleaned up"


# ── split_for_size_cap ─────────────────────────────────────────────

def test_split_for_size_cap_produces_sequential_non_overlapping_chunks(monkeypatch):
    monkeypatch.setattr(prep, "_require_ffmpeg", lambda: "ffmpeg")
    captured_cmds = []

    def fake_run(cmd, capture_output=True, check=True):
        captured_cmds.append(cmd)
        return _fake_run_writes_output(cmd, capture_output, check)

    monkeypatch.setattr(prep.subprocess, "run", fake_run)

    # min bitrate 24000 bps, target_bytes=24000 -> 8s per chunk.
    chunks = prep.split_for_size_cap("in.wav", 20.0, target_bytes=24_000)

    assert [(round(s, 3), round(e, 3)) for _p, s, e in chunks] == [
        (0.0, 8.0), (8.0, 16.0), (16.0, 20.0),
    ]
    for path, _s, _e in chunks:
        assert os.path.isfile(path)
    prep.cleanup_paths(p for p, _s, _e in chunks)

    for cmd in captured_cmds:
        assert "-af" in cmd and cmd[cmd.index("-af") + 1] == prep._LOUDNORM_FILTER


def test_split_for_size_cap_cleans_up_all_chunks_on_mid_loop_failure(monkeypatch):
    monkeypatch.setattr(prep, "_require_ffmpeg", lambda: "ffmpeg")
    calls = {"n": 0}

    def fake_run(cmd, capture_output=True, check=True):
        calls["n"] += 1
        if calls["n"] == 2:
            raise subprocess.CalledProcessError(1, cmd, stderr=b"boom")
        return _fake_run_writes_output(cmd, capture_output, check)

    monkeypatch.setattr(prep.subprocess, "run", fake_run)

    created = []
    real_ctor = prep.tempfile.NamedTemporaryFile

    def spying_ctor(*a, **k):
        tmp = real_ctor(*a, **k)
        created.append(tmp.name)
        return tmp

    monkeypatch.setattr(prep.tempfile, "NamedTemporaryFile", spying_ctor)

    with pytest.raises(prep.AudioPrepError, match="Groq"):
        prep.split_for_size_cap("in.wav", 20.0, target_bytes=24_000)

    assert len(created) == 2  # first chunk succeeded, second failed
    for path in created:
        assert not os.path.exists(path), f"{path} should have been cleaned up"


# ── cleanup_paths ──────────────────────────────────────────────────

def test_cleanup_paths_ignores_missing_files(tmp_path):
    present = tmp_path / "a.mp3"
    present.write_bytes(b"x")
    missing = str(tmp_path / "does_not_exist.mp3")
    prep.cleanup_paths([str(present), missing])
    assert not present.exists()


# ── _require_ffmpeg ────────────────────────────────────────────────

def test_require_ffmpeg_raises_actionable_message_when_missing(monkeypatch):
    monkeypatch.setattr(prep, "get_ffmpeg_path", lambda: None)
    with pytest.raises(prep.AudioPrepError, match="ffmpeg"):
        prep._require_ffmpeg()


# ── measure_mean_volume_db ───────────────────────────────────────────

def test_measure_mean_volume_db_parses_volumedetect_output(monkeypatch):
    monkeypatch.setattr(prep, "_require_ffmpeg", lambda: "ffmpeg")

    def fake_run(cmd, capture_output=True, check=True):
        stderr = (
            b"[Parsed_volumedetect_0 @ 0x0] mean_volume: -52.3 dB\n"
            b"[Parsed_volumedetect_0 @ 0x0] max_volume: -20.1 dB\n"
        )
        return MagicMock(returncode=0, stderr=stderr)

    monkeypatch.setattr(prep.subprocess, "run", fake_run)

    assert prep.measure_mean_volume_db("in.wav") == -52.3


def test_measure_mean_volume_db_treats_inf_as_extremely_quiet(monkeypatch):
    # Some ffmpeg builds report "mean_volume: -inf dB" for true digital
    # silence (rather than a finite noise-floor value like -91.0 dB) —
    # must resolve to a real, comparable float, not an opaque parse error.
    # Real ffmpeg volumedetect always emits both lines together.
    monkeypatch.setattr(prep, "_require_ffmpeg", lambda: "ffmpeg")

    def fake_run(cmd, capture_output=True, check=True):
        stderr = (
            b"[Parsed_volumedetect_0 @ 0x0] mean_volume: -inf dB\n"
            b"[Parsed_volumedetect_0 @ 0x0] max_volume: -inf dB\n"
        )
        return MagicMock(returncode=0, stderr=stderr)

    monkeypatch.setattr(prep.subprocess, "run", fake_run)

    result = prep.measure_mean_volume_db("in.wav")
    assert result == float("-inf")
    assert result < prep.QUIET_MEAN_VOLUME_DB_THRESHOLD


def test_measure_mean_volume_db_raises_when_output_unparseable(monkeypatch):
    monkeypatch.setattr(prep, "_require_ffmpeg", lambda: "ffmpeg")

    def fake_run(cmd, capture_output=True, check=True):
        return MagicMock(returncode=0, stderr=b"no useful info here\n")

    monkeypatch.setattr(prep.subprocess, "run", fake_run)

    with pytest.raises(prep.AudioPrepError, match="громкость"):
        prep.measure_mean_volume_db("in.wav")


def test_measure_mean_volume_db_raises_when_ffmpeg_missing(monkeypatch):
    monkeypatch.setattr(prep, "get_ffmpeg_path", lambda: None)
    with pytest.raises(prep.AudioPrepError, match="ffmpeg"):
        prep.measure_mean_volume_db("in.wav")


# ── measure_volume_stats ─────────────────────────────────────────────

def test_measure_volume_stats_parses_mean_and_max(monkeypatch):
    monkeypatch.setattr(prep, "_require_ffmpeg", lambda: "ffmpeg")

    def fake_run(cmd, capture_output=True, check=True):
        stderr = (
            b"[Parsed_volumedetect_0 @ 0x0] mean_volume: -52.3 dB\n"
            b"[Parsed_volumedetect_0 @ 0x0] max_volume: -20.1 dB\n"
        )
        return MagicMock(returncode=0, stderr=stderr)

    monkeypatch.setattr(prep.subprocess, "run", fake_run)

    mean_db, max_db = prep.measure_volume_stats("in.wav")
    assert mean_db == -52.3
    assert max_db == -20.1


def test_measure_volume_stats_treats_inf_as_extremely_quiet(monkeypatch):
    monkeypatch.setattr(prep, "_require_ffmpeg", lambda: "ffmpeg")

    def fake_run(cmd, capture_output=True, check=True):
        stderr = (
            b"[Parsed_volumedetect_0 @ 0x0] mean_volume: -inf dB\n"
            b"[Parsed_volumedetect_0 @ 0x0] max_volume: -inf dB\n"
        )
        return MagicMock(returncode=0, stderr=stderr)

    monkeypatch.setattr(prep.subprocess, "run", fake_run)

    mean_db, max_db = prep.measure_volume_stats("in.wav")
    assert mean_db == float("-inf")
    assert max_db == float("-inf")


def test_measure_volume_stats_raises_when_mean_missing(monkeypatch):
    monkeypatch.setattr(prep, "_require_ffmpeg", lambda: "ffmpeg")

    def fake_run(cmd, capture_output=True, check=True):
        stderr = b"[Parsed_volumedetect_0 @ 0x0] max_volume: -20.1 dB\n"
        return MagicMock(returncode=0, stderr=stderr)

    monkeypatch.setattr(prep.subprocess, "run", fake_run)

    with pytest.raises(prep.AudioPrepError, match="громкость"):
        prep.measure_volume_stats("in.wav")


def test_measure_volume_stats_raises_when_max_missing(monkeypatch):
    monkeypatch.setattr(prep, "_require_ffmpeg", lambda: "ffmpeg")

    def fake_run(cmd, capture_output=True, check=True):
        stderr = b"[Parsed_volumedetect_0 @ 0x0] mean_volume: -52.3 dB\n"
        return MagicMock(returncode=0, stderr=stderr)

    monkeypatch.setattr(prep.subprocess, "run", fake_run)

    with pytest.raises(prep.AudioPrepError, match="громкость"):
        prep.measure_volume_stats("in.wav")


def test_measure_volume_stats_raises_when_ffmpeg_missing(monkeypatch):
    monkeypatch.setattr(prep, "get_ffmpeg_path", lambda: None)
    with pytest.raises(prep.AudioPrepError, match="ffmpeg"):
        prep.measure_volume_stats("in.wav")


# ── should_rescue_quiet_audio ─────────────────────────────────────────

def test_should_rescue_quiet_audio_true_deep_below_mean_floor():
    assert prep.should_rescue_quiet_audio(-50.0, -30.0) is True


def test_should_rescue_quiet_audio_true_at_mean_floor_boundary():
    assert prep.should_rescue_quiet_audio(-45.0, -30.0) is True


def test_should_rescue_quiet_audio_transition_band_rescues_with_low_max():
    assert prep.should_rescue_quiet_audio(-44.9, -6.0) is True


def test_should_rescue_quiet_audio_transition_band_skips_with_loud_max():
    assert prep.should_rescue_quiet_audio(-44.9, -5.9) is False


def test_should_rescue_quiet_audio_at_upper_mean_boundary_with_low_max():
    assert prep.should_rescue_quiet_audio(-40.0, -6.0) is True


def test_should_rescue_quiet_audio_at_upper_mean_boundary_with_loud_max():
    assert prep.should_rescue_quiet_audio(-40.0, -5.9) is False


def test_should_rescue_quiet_audio_false_above_upper_mean_threshold():
    assert prep.should_rescue_quiet_audio(-39.9, -100.0) is False


def test_should_rescue_quiet_audio_false_for_pure_digital_silence():
    # Explicit invariant: no automatic upload gain on pure digital
    # silence — an -inf/-inf probe must never be opaque-parse-errored
    # into a rescue, and must not silently boost noise-floor artifacts.
    assert prep.should_rescue_quiet_audio(float("-inf"), float("-inf")) is False


def test_should_rescue_quiet_audio_false_when_only_mean_is_inf():
    assert prep.should_rescue_quiet_audio(float("-inf"), -20.0) is False


# ── prepare_quiet_audio_derivative ───────────────────────────────────

def test_prepare_quiet_audio_derivative_invokes_ffmpeg_with_conservative_loudnorm(monkeypatch):
    captured = {}

    def fake_run(cmd, capture_output=True, check=True):
        captured["cmd"] = cmd
        return _fake_run_writes_output(cmd, capture_output, check)

    monkeypatch.setattr(prep, "_require_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(prep, "_source_sample_rate_hz", lambda _path: 48_000, raising=False)
    monkeypatch.setattr(prep.subprocess, "run", fake_run)

    out_path = prep.prepare_quiet_audio_derivative("quiet.wav")

    assert os.path.isfile(out_path)
    assert out_path.endswith(".flac")
    os.unlink(out_path)

    cmd = captured["cmd"]
    assert cmd[0] == "ffmpeg"
    assert "-i" in cmd and cmd[cmd.index("-i") + 1] == "quiet.wav"
    assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "flac"
    # ffmpeg loudnorm dynamically up-samples to 192 kHz unless the output
    # rate is explicitly restored. Preserve the source rate after filtering;
    # omit -ac so channel count stays native.
    assert "-ar" in cmd and cmd[cmd.index("-ar") + 1] == "48000"
    assert "-ac" not in cmd
    af = cmd[cmd.index("-af") + 1]
    assert "highpass" not in af
    assert af == "loudnorm=I=-18:LRA=11:TP=-1.5"


def test_prepare_quiet_audio_derivative_raises_and_cleans_temp_on_ffmpeg_failure(monkeypatch):
    monkeypatch.setattr(prep, "_require_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(prep, "_source_sample_rate_hz", lambda _path: 16_000)

    def fake_run(cmd, capture_output=True, check=True):
        raise subprocess.CalledProcessError(1, cmd, stderr=b"boom")

    monkeypatch.setattr(prep.subprocess, "run", fake_run)

    seen_paths = []
    real_tempfile_ctor = prep.tempfile.NamedTemporaryFile

    def spying_ctor(*a, **k):
        tmp = real_tempfile_ctor(*a, **k)
        seen_paths.append(tmp.name)
        return tmp

    monkeypatch.setattr(prep.tempfile, "NamedTemporaryFile", spying_ctor)

    with pytest.raises(prep.AudioPrepError, match="тих"):
        prep.prepare_quiet_audio_derivative("quiet.wav")

    assert seen_paths, "expected a temp file to have been created"
    assert not os.path.exists(seen_paths[0]), "failed temp output must be cleaned up"
