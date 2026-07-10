"""Tests for the ASR-only quiet-audio gate in the cloud transcription
dispatcher (transcriber._run_cloud_stt).

Quiet source audio (mean volume below
``audio_upload_prep.QUIET_MEAN_VOLUME_DB_THRESHOLD``) can reach a cloud
provider too faint to transcribe. The gate measures loudness locally via
ffmpeg BEFORE an ordinary upload and, only for quiet inputs, uploads a
highpass+loudnorm-normalized temporary derivative instead of the raw file.

No real ffmpeg, no HTTP: audio_upload_prep's ffmpeg-invoking functions are
monkeypatched, mirroring the pattern in
tests/test_transcriber_groq_free_tier.py.
"""
from __future__ import annotations

import hashlib
import os

import pytest

import audio_upload_prep
from providers import PROVIDERS, ProviderError, TranscriptionProvider, TranscriptionResult
from transcriber import Transcriber


def _register_fake(monkeypatch, *, max_upload_bytes=None, transcribe_impl=None):
    """Register a fake provider under 'QuietFake'. ``calls`` records every
    audio_path the fake's transcribe() was actually invoked with, in order."""
    calls: list[str] = []
    cap = max_upload_bytes

    class _Fake(TranscriptionProvider):
        display_name = "QuietFake"
        supports_diarization = False
        max_upload_bytes = cap

        def __init__(self, api_key):
            self.api_key = api_key

        def transcribe(
            self, audio_path, options, on_status=None, on_progress=None,
            cancel_event=None,
        ):
            calls.append(audio_path)
            if transcribe_impl is not None:
                return transcribe_impl(audio_path, options)
            return TranscriptionResult(
                segments=[{"start": 0.0, "end": 1.0, "text": "ok"}]
            )

    monkeypatch.setitem(PROVIDERS, "QuietFake", _Fake)
    return calls


# ── quiet under-cap: derivative uploaded then removed ────────────────

def test_quiet_audio_uploads_derivative_and_cleans_it_up(tmp_path, monkeypatch):
    audio = tmp_path / "quiet.wav"
    audio.write_bytes(b"\x00" * 100)
    original_hash = hashlib.sha256(audio.read_bytes()).hexdigest()

    calls = _register_fake(monkeypatch)

    derivative = tmp_path / "derivative.mp3"
    derivative.write_bytes(b"\x00" * 50)

    monkeypatch.setattr(audio_upload_prep, "measure_volume_stats", lambda p: (-55.0, -55.0))
    monkeypatch.setattr(
        audio_upload_prep, "prepare_quiet_audio_derivative",
        lambda p: str(derivative),
    )

    Transcriber().transcribe(
        str(audio), cloud_provider="QuietFake", cloud_api_key="k",
    )

    assert calls == [str(derivative)]
    assert not derivative.exists(), "quiet derivative must be cleaned up after success"
    assert audio.exists()
    assert hashlib.sha256(audio.read_bytes()).hexdigest() == original_hash


# ── borderline transition-band: max-volume guard drives the decision ──

def test_borderline_quiet_mean_with_loud_transient_skips_rescue(tmp_path, monkeypatch):
    audio = tmp_path / "borderline_loud_transient.wav"
    audio.write_bytes(b"\x00" * 100)
    calls = _register_fake(monkeypatch)

    monkeypatch.setattr(audio_upload_prep, "measure_volume_stats", lambda p: (-42.0, -3.0))

    def _boom(*a, **k):
        raise AssertionError("prepare_quiet_audio_derivative must not run when max guard trips")

    monkeypatch.setattr(audio_upload_prep, "prepare_quiet_audio_derivative", _boom)

    Transcriber().transcribe(
        str(audio), cloud_provider="QuietFake", cloud_api_key="k",
    )

    assert calls == [str(audio)]


def test_borderline_quiet_mean_without_loud_transient_triggers_rescue(tmp_path, monkeypatch):
    audio = tmp_path / "borderline_uniform_quiet.wav"
    audio.write_bytes(b"\x00" * 100)
    calls = _register_fake(monkeypatch)

    derivative = tmp_path / "borderline_derivative.flac"
    derivative.write_bytes(b"\x00" * 50)

    monkeypatch.setattr(audio_upload_prep, "measure_volume_stats", lambda p: (-42.0, -10.0))
    monkeypatch.setattr(
        audio_upload_prep, "prepare_quiet_audio_derivative",
        lambda p: str(derivative),
    )

    Transcriber().transcribe(
        str(audio), cloud_provider="QuietFake", cloud_api_key="k",
    )

    assert calls == [str(derivative)]


# ── pure digital silence: no automatic upload gain ────────────────────

def test_pure_digital_silence_skips_automatic_rescue(tmp_path, monkeypatch):
    audio = tmp_path / "silence.wav"
    audio.write_bytes(b"\x00" * 100)
    calls = _register_fake(monkeypatch)

    monkeypatch.setattr(
        audio_upload_prep, "measure_volume_stats",
        lambda p: (float("-inf"), float("-inf")),
    )

    def _boom(*a, **k):
        raise AssertionError("prepare_quiet_audio_derivative must not run on pure silence")

    monkeypatch.setattr(audio_upload_prep, "prepare_quiet_audio_derivative", _boom)

    Transcriber().transcribe(
        str(audio), cloud_provider="QuietFake", cloud_api_key="k",
    )

    assert calls == [str(audio)]


# ── normal (loud enough) under-cap: original uploaded directly ───────

def test_normal_volume_audio_uploads_original_no_derivative(tmp_path, monkeypatch):
    audio = tmp_path / "normal.wav"
    audio.write_bytes(b"\x00" * 100)
    calls = _register_fake(monkeypatch)

    monkeypatch.setattr(audio_upload_prep, "measure_volume_stats", lambda p: (-10.0, -3.0))

    def _boom(*a, **k):
        raise AssertionError("prepare_quiet_audio_derivative must not run for loud audio")

    monkeypatch.setattr(audio_upload_prep, "prepare_quiet_audio_derivative", _boom)

    Transcriber().transcribe(
        str(audio), cloud_provider="QuietFake", cloud_api_key="k",
    )

    assert calls == [str(audio)]


# ── capped original: no redundant quiet-prep ──────────────────────────

def test_over_cap_original_skips_quiet_measurement_entirely(tmp_path, monkeypatch):
    """A file already over the provider's cap must never be probed for
    loudness — its existing cap preparation already normalizes."""
    audio = tmp_path / "big.wav"
    audio.write_bytes(b"\x00" * 5000)
    calls = _register_fake(monkeypatch, max_upload_bytes=1000)

    def _boom(*a, **k):
        raise AssertionError("measure_volume_stats must not run over cap")

    monkeypatch.setattr(audio_upload_prep, "measure_volume_stats", _boom)

    compressed = tmp_path / "compressed.mp3"
    compressed.write_bytes(b"\x00" * 500)
    monkeypatch.setattr(
        "processing.preflight.probe",
        lambda p: {"duration_s": 60.0, "size_bytes": 5000},
    )
    monkeypatch.setattr(
        audio_upload_prep, "compress_for_size_cap",
        lambda *a, **k: (str(compressed), True),
    )

    Transcriber().transcribe(
        str(audio), cloud_provider="QuietFake", cloud_api_key="k",
    )

    assert calls == [str(compressed)]


# ── quiet derivative itself over cap: falls through to cap-prep ──────

def test_quiet_derivative_over_cap_routes_through_cap_prep_and_cleans_all_temps(
    tmp_path, monkeypatch,
):
    """A quiet ORIGINAL that's under the cap can still produce a quiet
    derivative that itself exceeds the cap (e.g. loudnorm/highpass at a
    higher bitrate than the raw source). That derivative must be handed to
    the existing compress/chunk cap-prep path — not uploaded raw — and
    every temp (quiet derivative + cap-prep derivative) must be cleaned
    up afterwards."""
    audio = tmp_path / "quiet_small.wav"
    audio.write_bytes(b"\x00" * 100)  # under the 1000-byte cap
    calls = _register_fake(monkeypatch, max_upload_bytes=1000)

    quiet_derivative = tmp_path / "quiet_derivative.mp3"
    quiet_derivative.write_bytes(b"\x00" * 5000)  # over the cap once boosted

    compressed = tmp_path / "cap_compressed.mp3"
    compressed.write_bytes(b"\x00" * 500)  # under the cap

    monkeypatch.setattr(audio_upload_prep, "measure_volume_stats", lambda p: (-55.0, -55.0))
    monkeypatch.setattr(
        audio_upload_prep, "prepare_quiet_audio_derivative",
        lambda p: str(quiet_derivative),
    )
    monkeypatch.setattr(
        "processing.preflight.probe",
        lambda p: {"duration_s": 60.0, "size_bytes": 5000},
    )
    monkeypatch.setattr(
        audio_upload_prep, "compress_for_size_cap",
        lambda path, duration_s, target_bytes: (str(compressed), True),
    )

    Transcriber().transcribe(
        str(audio), cloud_provider="QuietFake", cloud_api_key="k",
    )

    assert calls == [str(compressed)]
    assert not quiet_derivative.exists(), "quiet derivative temp must be cleaned up"
    assert not compressed.exists(), "cap-prep derivative temp must be cleaned up"
    assert audio.exists()


# ── unprobeable input: preserve legacy provider path ──────────────────

def test_unprobeable_audio_falls_back_to_original_provider_upload(tmp_path, monkeypatch):
    """The quality gate must not mask an existing provider error path when
    a caller/test supplies an unreadable or virtual audio path. Measurement is
    best-effort; the actual provider still decides whether it can accept it."""
    audio = tmp_path / "virtual.wav"
    audio.write_bytes(b"not a real wav")
    calls = _register_fake(monkeypatch)

    def _measure_error(_path):
        raise audio_upload_prep.AudioPrepError("ffmpeg cannot inspect fixture")

    monkeypatch.setattr(audio_upload_prep, "measure_volume_stats", _measure_error)

    Transcriber().transcribe(
        str(audio), cloud_provider="QuietFake", cloud_api_key="k",
    )

    assert calls == [str(audio)]
