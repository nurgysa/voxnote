"""Tests for providers.base — ABC defaults and TranscriptionOptions contract."""

from providers.base import (
    TranscriptionOptions,
    TranscriptionProvider,
    TranscriptionResult,
)


class _StubProvider(TranscriptionProvider):
    """Minimal subclass that only implements the abstract transcribe(),
    so we can probe inherited behavior like supports_mixed."""

    display_name = "Stub"
    supports_diarization = False

    def transcribe(self, audio_path, options, on_status=None, on_progress=None, cancel_event=None):
        return TranscriptionResult(segments=[])


def test_supports_mixed_default_true():
    """ABC default: providers opt in to 'mixed' unless they explicitly
    declare otherwise. The 4 of 5 providers that support KZ/RU/EN ride
    the default; Deepgram overrides to False (lacks KZ in nova-3)."""
    p = _StubProvider()
    assert p.supports_mixed is True


def test_transcription_options_accepts_mixed_language():
    """The dataclass shouldn't reject the new sentinel string —
    .language is typed `str | None` with no validator."""
    opts = TranscriptionOptions(language="mixed")
    assert opts.language == "mixed"
