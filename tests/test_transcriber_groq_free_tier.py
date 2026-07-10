"""Tests for the Groq-style free-tier (max_upload_bytes) preparation path
in the cloud transcription dispatcher (transcriber._run_cloud_stt).

No real ffmpeg, no HTTP: audio_upload_prep's ffmpeg-invoking functions are
monkeypatched so these tests are fast and hermetic — same fake-provider
pattern as tests/test_transcriber_dispatch.py.
"""
from __future__ import annotations

import hashlib
import os

import pytest

import audio_upload_prep
from providers import (
    PROVIDERS,
    ProviderError,
    TranscriptionProvider,
    TranscriptionResult,
)
from transcriber import Transcriber


def _register_capped_fake(monkeypatch, *, max_upload_bytes, transcribe_impl=None):
    """Register a fake capped provider under 'CappedFake'. ``calls`` records
    every audio_path the fake's transcribe() was actually invoked with, in
    order — the assertion surface for "what did we actually upload"."""
    calls: list[str] = []
    # Class-body assignment can't read the same name off the enclosing
    # function scope (self-referential lookup), hence the local alias.
    cap = max_upload_bytes

    class _Capped(TranscriptionProvider):
        display_name = "CappedFake"
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

    monkeypatch.setitem(PROVIDERS, "CappedFake", _Capped)
    return calls


# ── no-op below the cap ────────────────────────────────────────────

def test_under_cap_file_skips_prep_entirely(tmp_path, monkeypatch):
    """A file already under the provider's cap must never trigger
    compression/chunking — the default path stays untouched."""
    audio = tmp_path / "small.wav"
    audio.write_bytes(b"\x00" * 100)
    calls = _register_capped_fake(monkeypatch, max_upload_bytes=1000)

    def _boom(*a, **k):
        raise AssertionError("compress_for_size_cap must not run under cap")

    monkeypatch.setattr(audio_upload_prep, "compress_for_size_cap", _boom)
    # Loud enough that the quiet-audio gate (tests/test_transcriber_quiet_audio_gate.py)
    # leaves this file untouched too.
    monkeypatch.setattr(audio_upload_prep, "measure_volume_stats", lambda p: (-10.0, -3.0))

    Transcriber().transcribe(
        str(audio), cloud_provider="CappedFake", cloud_api_key="k",
    )
    assert calls == [str(audio)]


# ── compression path ────────────────────────────────────────────────

def test_over_cap_file_is_compressed_then_transcribed_once(tmp_path, monkeypatch):
    audio = tmp_path / "big.wav"
    audio.write_bytes(b"\x00" * 5000)
    calls = _register_capped_fake(monkeypatch, max_upload_bytes=1000)

    compressed_path = tmp_path / "compressed.mp3"
    compressed_path.write_bytes(b"\x00" * 500)  # under cap

    monkeypatch.setattr(
        "processing.preflight.probe",
        lambda p: {"duration_s": 120.0, "size_bytes": 5000},
    )
    monkeypatch.setattr(
        audio_upload_prep, "compress_for_size_cap",
        lambda path, duration_s, target_bytes: (str(compressed_path), True),
    )

    Transcriber().transcribe(
        str(audio), cloud_provider="CappedFake", cloud_api_key="k",
    )

    assert calls == [str(compressed_path)]
    assert not compressed_path.exists(), "compressed temp must be cleaned up after success"


def test_original_audio_file_is_never_mutated_by_prep(tmp_path, monkeypatch):
    """Provenance guarantee: the raw file's bytes/hash must be identical
    before and after transcribe(), and the provider must never see the
    original path once a derivative was produced."""
    audio = tmp_path / "original.wav"
    audio.write_bytes(b"ORIGINAL-BYTES" * 200)
    original_hash = hashlib.sha256(audio.read_bytes()).hexdigest()

    calls = _register_capped_fake(monkeypatch, max_upload_bytes=1000)
    compressed_path = tmp_path / "derivative.mp3"
    compressed_path.write_bytes(b"\x00" * 500)

    monkeypatch.setattr(
        "processing.preflight.probe",
        lambda p: {"duration_s": 60.0, "size_bytes": os.path.getsize(str(audio))},
    )
    monkeypatch.setattr(
        audio_upload_prep, "compress_for_size_cap",
        lambda *a, **k: (str(compressed_path), True),
    )

    Transcriber().transcribe(
        str(audio), cloud_provider="CappedFake", cloud_api_key="k",
    )

    assert audio.exists()
    assert hashlib.sha256(audio.read_bytes()).hexdigest() == original_hash
    assert calls == [str(compressed_path)]


# ── chunk + merge path ──────────────────────────────────────────────

def test_compression_over_cap_falls_back_to_chunking_and_merges_offsets(tmp_path, monkeypatch):
    audio = tmp_path / "huge.wav"
    audio.write_bytes(b"\x00" * 5000)

    chunk_a = tmp_path / "chunk_a.mp3"
    chunk_b = tmp_path / "chunk_b.mp3"
    chunk_a.write_bytes(b"\x00" * 500)
    chunk_b.write_bytes(b"\x00" * 500)

    calls: list[str] = []

    def transcribe_impl(path, options):
        # Providers only ever see their own chunk file, so they report
        # LOCAL (chunk-relative) timestamps starting at 0 — the dispatcher
        # must shift these by the chunk's offset when merging.
        calls.append(path)
        idx = len(calls) - 1
        return TranscriptionResult(
            segments=[{"start": 0.0, "end": 5.0, "text": f"part-{idx}"}],
            language="ru", model="whisper-large-v3-turbo",
        )

    class _Capped(TranscriptionProvider):
        display_name = "CappedFake"
        supports_diarization = False
        max_upload_bytes = 1000

        def __init__(self, api_key):
            pass

        def transcribe(self, audio_path, options, on_status=None,
                        on_progress=None, cancel_event=None):
            return transcribe_impl(audio_path, options)

    monkeypatch.setitem(PROVIDERS, "CappedFake", _Capped)

    monkeypatch.setattr(
        "processing.preflight.probe",
        lambda p: {"duration_s": 60.0, "size_bytes": 5000},
    )
    # Compression alone doesn't fit -> orchestrator falls through to chunking.
    monkeypatch.setattr(
        audio_upload_prep, "compress_for_size_cap", lambda *a, **k: None,
    )
    monkeypatch.setattr(
        audio_upload_prep, "split_for_size_cap",
        lambda *a, **k: [
            (str(chunk_a), 0.0, 30.0),
            (str(chunk_b), 30.0, 60.0),
        ],
    )

    t = Transcriber()
    t.transcribe(str(audio), cloud_provider="CappedFake", cloud_api_key="k")

    assert calls == [str(chunk_a), str(chunk_b)]
    assert t.last_segments == [
        {"start": 0.0, "end": 5.0, "text": "part-0"},
        {"start": 30.0, "end": 35.0, "text": "part-1"},
    ]
    assert not chunk_a.exists()
    assert not chunk_b.exists()


def test_chunk_transcribe_failure_still_cleans_up_all_chunk_files(tmp_path, monkeypatch):
    audio = tmp_path / "huge.wav"
    audio.write_bytes(b"\x00" * 5000)
    chunk_a = tmp_path / "chunk_a.mp3"
    chunk_b = tmp_path / "chunk_b.mp3"
    chunk_a.write_bytes(b"\x00" * 500)
    chunk_b.write_bytes(b"\x00" * 500)

    def transcribe_impl(path, options):
        if path == str(chunk_b):
            raise ProviderError("Groq квота исчерпана")
        return TranscriptionResult(segments=[{"start": 0.0, "end": 5.0, "text": "ok"}])

    _register_capped_fake(monkeypatch, max_upload_bytes=1000, transcribe_impl=transcribe_impl)

    monkeypatch.setattr(
        "processing.preflight.probe",
        lambda p: {"duration_s": 60.0, "size_bytes": 5000},
    )
    monkeypatch.setattr(audio_upload_prep, "compress_for_size_cap", lambda *a, **k: None)
    monkeypatch.setattr(
        audio_upload_prep, "split_for_size_cap",
        lambda *a, **k: [
            (str(chunk_a), 0.0, 30.0),
            (str(chunk_b), 30.0, 60.0),
        ],
    )

    with pytest.raises(RuntimeError):
        Transcriber().transcribe(
            str(audio), cloud_provider="CappedFake", cloud_api_key="k",
        )

    assert not chunk_a.exists()
    assert not chunk_b.exists()


# ── failure paths ────────────────────────────────────────────────────

def test_unknown_duration_raises_actionable_error(tmp_path, monkeypatch):
    audio = tmp_path / "huge.wav"
    audio.write_bytes(b"\x00" * 5000)
    _register_capped_fake(monkeypatch, max_upload_bytes=1000)

    monkeypatch.setattr(
        "processing.preflight.probe",
        lambda p: {"duration_s": None, "size_bytes": 5000},
    )

    with pytest.raises(RuntimeError, match="длительность"):
        Transcriber().transcribe(
            str(audio), cloud_provider="CappedFake", cloud_api_key="k",
        )


def test_ffmpeg_missing_surfaces_as_runtimeerror(tmp_path, monkeypatch):
    audio = tmp_path / "huge.wav"
    audio.write_bytes(b"\x00" * 5000)
    _register_capped_fake(monkeypatch, max_upload_bytes=1000)

    monkeypatch.setattr(
        "processing.preflight.probe",
        lambda p: {"duration_s": 60.0, "size_bytes": 5000},
    )
    monkeypatch.setattr(audio_upload_prep, "get_ffmpeg_path", lambda: None)

    with pytest.raises(RuntimeError, match="ffmpeg"):
        Transcriber().transcribe(
            str(audio), cloud_provider="CappedFake", cloud_api_key="k",
        )


def test_chunk_still_over_cap_raises_actionable_error(tmp_path, monkeypatch):
    audio = tmp_path / "huge.wav"
    audio.write_bytes(b"\x00" * 5000)
    _register_capped_fake(monkeypatch, max_upload_bytes=1000)

    oversized_chunk = tmp_path / "oversized.mp3"
    oversized_chunk.write_bytes(b"\x00" * 2000)  # still over the 1000-byte cap

    monkeypatch.setattr(
        "processing.preflight.probe",
        lambda p: {"duration_s": 60.0, "size_bytes": 5000},
    )
    monkeypatch.setattr(audio_upload_prep, "compress_for_size_cap", lambda *a, **k: None)
    monkeypatch.setattr(
        audio_upload_prep, "split_for_size_cap",
        lambda *a, **k: [(str(oversized_chunk), 0.0, 60.0)],
    )

    with pytest.raises(RuntimeError, match="25"):
        Transcriber().transcribe(
            str(audio), cloud_provider="CappedFake", cloud_api_key="k",
        )
