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
