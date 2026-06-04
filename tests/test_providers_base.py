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
