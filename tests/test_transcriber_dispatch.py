"""Characterization tests for the cloud transcription dispatcher.

The orchestration seam was previously untested: ``cli/core`` stubs the whole
``transcriber`` module via ``sys.modules`` injection, and the UI path only
runs at GUI runtime. These tests exercise the REAL
``Transcriber.transcribe`` / ``_transcribe_via_cloud`` / ``_run_cloud_stt``
against a fake in-memory provider — no HTTP, no ffmpeg, no real audio file.

They pin the contracts the UI exception handler + save dialog depend on:
missing-credential guard, ProviderError→RuntimeError re-wrap, formatter
selection by speaker presence, last_segments caching, and denoise-tempfile
cleanup on the error path.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from providers import (
    PROVIDERS,
    ProviderError,
    TranscriptionProvider,
    TranscriptionResult,
)
from transcriber import Transcriber
from transcript_format import format_diarized, format_timed


def _register_fake(monkeypatch, *, segments=None, error=None):
    """Register a fake provider under 'Fake' for the duration of the test.

    ``segments`` is returned from transcribe(); ``error`` (if given) is raised
    instead — both let a test drive the dispatcher's branches without a network.
    """
    class _Fake(TranscriptionProvider):
        display_name = "Fake"
        supports_diarization = True

        def __init__(self, api_key):
            self.api_key = api_key

        def transcribe(
            self, audio_path, options, on_status=None, on_progress=None, cancel_event=None
        ):
            if error is not None:
                raise error
            return TranscriptionResult(segments=list(segments or []))

    monkeypatch.setitem(PROVIDERS, "Fake", _Fake)
    return _Fake


def test_missing_provider_or_key_raises_valueerror():
    """The cloud-only build has no local fallback — cloud_provider AND
    cloud_api_key are both mandatory (empty/None must fail fast, before HTTP)."""
    t = Transcriber()
    with pytest.raises(ValueError):
        t.transcribe("a.wav", cloud_provider=None, cloud_api_key="k")
    with pytest.raises(ValueError):
        t.transcribe("a.wav", cloud_provider="Fake", cloud_api_key="")


def test_diarized_segments_use_format_diarized(monkeypatch):
    """diarize=True AND a segment carries a 'speaker' key → format_diarized."""
    segs = [
        {"start": 0.0, "end": 1.0, "text": "привет", "speaker": "SPEAKER_0"},
        {"start": 1.0, "end": 2.0, "text": "пока", "speaker": "SPEAKER_1"},
    ]
    _register_fake(monkeypatch, segments=segs)
    out = Transcriber().transcribe(
        "a.wav", diarize=True, cloud_provider="Fake", cloud_api_key="k",
    )
    assert out == format_diarized(segs)


def test_no_speaker_segments_use_format_timed(monkeypatch):
    """diarize=True but NO segment carries a speaker → falls back to
    format_timed (the has_speakers guard prevents an empty-speaker diarized view)."""
    segs = [
        {"start": 0.0, "end": 1.0, "text": "привет"},
        {"start": 1.0, "end": 2.0, "text": "пока"},
    ]
    _register_fake(monkeypatch, segments=segs)
    out = Transcriber().transcribe(
        "a.wav", diarize=True, cloud_provider="Fake", cloud_api_key="k",
    )
    assert out == format_timed(segs)


def test_provider_error_rewrapped_as_runtimeerror(monkeypatch):
    """A ProviderError surfaces as RuntimeError with the message preserved and
    the original cause attached — the contract ui/app's except arm relies on."""
    err = ProviderError("боом: квота исчерпана")
    _register_fake(monkeypatch, error=err)
    with pytest.raises(RuntimeError) as ei:
        Transcriber().transcribe("a.wav", cloud_provider="Fake", cloud_api_key="k")
    assert str(ei.value) == "боом: квота исчерпана"
    assert ei.value.__cause__ is err


def test_last_segments_cached_for_export(monkeypatch):
    """last_segments is populated after a run so the save dialog can export
    SRT/VTT without re-transcribing."""
    segs = [{"start": 0.0, "end": 1.0, "text": "x"}]
    _register_fake(monkeypatch, segments=segs)
    t = Transcriber()
    t.transcribe("a.wav", cloud_provider="Fake", cloud_api_key="k")
    assert t.last_segments == segs


def test_denoise_tempfile_cleaned_on_provider_error(monkeypatch):
    """When denoise_audio=True the dispatcher denoises to a temp WAV via
    ensure_wav; that temp must be unlinked in the finally even when the
    provider then raises."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.write(b"RIFF")
    tmp.close()
    # ensure_wav is bound into the transcriber namespace at module import.
    monkeypatch.setattr("transcriber.ensure_wav", lambda *a, **k: (tmp.name, True))
    _register_fake(monkeypatch, error=ProviderError("fail after denoise"))
    with pytest.raises(RuntimeError):
        Transcriber().transcribe(
            "a.wav", denoise_audio=True, cloud_provider="Fake", cloud_api_key="k",
        )
    assert not os.path.exists(tmp.name), "denoised tempfile must be cleaned in finally"


def test_transcribe_threads_speaker_id_and_caches(monkeypatch, tmp_path):
    import transcriber as tmod
    from providers.base import TranscriptionResult

    captured = {}

    class FakeProvider:
        supports_mixed = True
        supports_speaker_id = True
        def transcribe(self, path, opts, on_status=None, on_progress=None,
                       cancel_event=None):
            captured["enroll"] = opts.enroll_speakers
            captured["known"] = opts.known_speakers
            return TranscriptionResult(
                segments=[{"start": 0.0, "end": 1.0, "text": "hi",
                           "speaker": "Айбек Нурланов"}],
                language="ru",
                speaker_identifiers={"Айбек Нурланов": ["id-b"]},
                model="m-x",
            )

    import providers
    monkeypatch.setattr(providers, "get_provider", lambda *a, **k: FakeProvider())

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"\x00" * 16)

    t = tmod.Transcriber()
    t.transcribe(
        str(audio), diarize=True, cloud_provider="Speechmatics",
        cloud_api_key="k", enroll_speakers=True,
        known_speakers=[{"label": "Айбек Нурланов", "identifiers": ["id-b"]}],
    )
    assert captured["enroll"] is True
    assert captured["known"] == [{"label": "Айбек Нурланов", "identifiers": ["id-b"]}]
    assert t.last_speaker_identifiers == {"Айбек Нурланов": ["id-b"]}
    assert t.last_model == "m-x"


def test_last_speaker_identifiers_default_none():
    import transcriber as tmod
    t = tmod.Transcriber()
    assert t.last_speaker_identifiers is None
    assert t.last_model is None
