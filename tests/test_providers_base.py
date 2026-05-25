"""Tests for providers.base — ABC defaults and TranscriptionOptions contract."""

import pytest

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


def test_supports_mixed_default_false():
    """ABC default: providers must explicitly opt in to mixed-mode support by
    setting ``supports_mixed = True`` once their ``_submit()`` has a
    mixed-aware branch. This keeps the capability map honest at every commit
    during the phased PR-B/PR-C rollout and avoids exposing
    'Смешанный (KZ+RU+EN)' as selectable for providers that aren't wired yet.
    Providers that do support it override the attribute to True explicitly."""
    p = _StubProvider()
    assert p.supports_mixed is False


def test_max_upload_bytes_default_none():
    """ABC default for max_upload_bytes is None — meaning "no provider-side
    hard cap, cloud_chunker should NOT split based on this provider's
    upload limit". Providers with a documented small cap (Groq Free 25 MB,
    OpenAI whisper-1 25 MB) override this to the byte value; the chunker
    consults the attribute to decide whether to split a file before
    upload."""
    p = _StubProvider()
    assert p.max_upload_bytes is None


def test_groq_advertises_25mb_upload_cap():
    """Groq Free tier hard cap. Test pins the value so a future tier-class
    edit (or a docstring drift) doesn't accidentally remove the cap and
    let the chunker pass oversized files through."""
    from providers.groq import GroqProvider
    assert GroqProvider.max_upload_bytes == 25 * 1024 * 1024


def test_openai_whisper_advertises_25mb_upload_cap():
    """OpenAI whisper-1 gateway has the same 25 MB cap. Pinned for the
    same reason as Groq."""
    from providers.openai_whisper import OpenAIWhisperProvider
    assert OpenAIWhisperProvider.max_upload_bytes == 25 * 1024 * 1024


def test_other_providers_have_no_cap():
    """Deepgram/Gladia/AssemblyAI/Speechmatics all advertise multi-GB
    upload limits in their docs — None means the chunker won't split
    based on provider cap (it may still split for other reasons in
    a future PR, but that's not driven by these provider classes)."""
    from providers.assemblyai import AssemblyAIProvider
    from providers.deepgram import DeepgramProvider
    from providers.gladia import GladiaProvider
    from providers.speechmatics import SpeechmaticsProvider
    for cls in (
        AssemblyAIProvider,
        DeepgramProvider,
        GladiaProvider,
        SpeechmaticsProvider,
    ):
        assert cls.max_upload_bytes is None, (
            f"{cls.__name__} unexpectedly declares a max_upload_bytes; "
            f"if their docs added a cap, update this test with the new "
            f"value AND verify the chunker can produce chunks that fit."
        )


def test_transcription_options_accepts_mixed_language():
    """The dataclass shouldn't reject the new sentinel string —
    .language is typed `str | None` with no validator."""
    opts = TranscriptionOptions(language="mixed")
    assert opts.language == "mixed"


# ---------------------------------------------------------------------------
# Runtime guard: transcribe() cloud short-circuit
# ---------------------------------------------------------------------------

def test_transcribe_blocks_mixed_for_unsupported_cloud_provider(monkeypatch):
    """When language='mixed' is dispatched to a cloud provider whose
    ``supports_mixed`` class attribute is False, ``Transcriber.transcribe()``
    must raise ``ProviderError`` BEFORE any HTTP work (specifically before
    ``_transcribe_via_cloud`` is entered)."""
    from providers import PROVIDERS, ProviderError, TranscriptionProvider
    from transcriber import Transcriber

    class _Unsupported(TranscriptionProvider):
        display_name = "TestUnsupported"
        supports_diarization = False
        supports_mixed = False  # explicit for clarity

        def __init__(self, api_key: str):
            self._api_key = api_key

        def transcribe(
            self, audio_path, options, on_status=None, on_progress=None, cancel_event=None
        ):
            raise AssertionError(
                "should never reach the provider — guard must fire first"
            )

    monkeypatch.setitem(PROVIDERS, "TestUnsupported", _Unsupported)

    t = Transcriber()
    with pytest.raises(ProviderError, match="не поддерживает"):
        t.transcribe(
            audio_path="dummy.wav",
            language="mixed",
            cloud_provider="TestUnsupported",
            cloud_api_key="x",
        )
