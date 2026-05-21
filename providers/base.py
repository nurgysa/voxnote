"""Abstract base class for cloud transcription providers.

A provider takes an audio file and returns a list of segments in the same
shape the local Whisper+pyannote pipeline produces — so downstream
formatters (`format_timed`, `format_diarized`, `format_srt`, `format_vtt`)
work unchanged regardless of where transcription happened.

Adding a new provider (Deepgram, OpenAI Whisper, Replicate, …) means:

1. Subclass ``TranscriptionProvider``.
2. Implement ``transcribe()``.
3. Register the class in ``providers/__init__.py``.

The subclass owns its API client, error handling, and progress reporting.
The Transcriber class only knows about this abstract interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class TranscriptionOptions:
    """Per-call options. Providers map these to their native API params."""

    language: str | None = None        # "ru" | "kk" | "en" | "mixed" | None=auto
    # "mixed" is the KZ+RU+EN code-switching sentinel; providers branch on
    # it in _submit() and enable their native multilingual mode. Providers
    # that can't handle one of KZ/RU/EN declare supports_mixed() -> False
    # and raise ProviderError when called with language="mixed".
    diarize: bool = False              # Request speaker labels.
    hotwords: list[str] = field(default_factory=list)
    num_speakers: int | None = None    # Exact speaker count, when known.
    min_speakers: int | None = None    # Lower bound (when num_speakers None).
    max_speakers: int | None = None    # Upper bound (when num_speakers None).


@dataclass
class TranscriptionResult:
    """Provider-agnostic result.

    ``segments`` matches the shape the local pipeline emits:
        {"start": float, "end": float, "text": str, "speaker"?: str}
    so the same TXT/SRT/VTT formatters work for both paths.
    """

    segments: list[dict]
    language: str | None = None        # Detected language code (if returned).
    raw: dict | None = None            # Original API response (for debugging).


class ProviderError(RuntimeError):
    """Base for provider-side failures.

    Wraps HTTP/auth/quota/timeout problems with a user-facing Russian
    message. The original cause (if any) is attached via ``__cause__``;
    Transcriber turns these into a TK error dialog without leaking
    request/response details.
    """


class TranscriptionProvider(ABC):
    """Cloud transcription backend interface.

    Subclasses are expected to be cheap to construct — keep heavy state
    (HTTP sessions, etc.) lazy. Each ``transcribe()`` call is a single
    job; the provider must honour ``cancel_event`` between long
    operations (uploads, polls).
    """

    #: Human-readable name shown in the Settings dropdown.
    display_name: str = ""

    #: True when the provider returns speaker labels (so the
    #: "Диаризация" checkbox is meaningful in cloud mode).
    supports_diarization: bool = False

    #: Whether this provider supports the KZ+RU+EN code-switching mode.
    #: Default True — all currently-supported providers EXCEPT Deepgram
    #: ship KZ in their multilingual models. Deepgram's nova-3 omits KZ
    #: and overrides this to False (``supports_mixed = False``), then
    #: raises ProviderError when called with ``options.language == "mixed"``.
    #: Used by Settings UI to surface an inline warning when the current
    #: provider can't service a stored 'Смешанный (KZ+RU+EN)' language
    #: preference. Class attribute (not a method) to mirror
    #: ``supports_diarization``; static introspectable capability.
    supports_mixed: bool = True

    @abstractmethod
    def transcribe(
        self,
        audio_path: str,
        options: TranscriptionOptions,
        on_status=None,
        on_progress=None,
        cancel_event=None,
    ) -> TranscriptionResult:
        """Run transcription and return segments.

        Args:
            audio_path: Local file (provider uploads it). Pre-normalised
                WAV from ``ensure_wav`` is fine — saves them re-decoding.
            options: Language / hotwords / diarization request.
            on_status: Optional ``callable(text: str)`` for UI status line.
            on_progress: Optional ``callable(percent: float)`` 0..100.
            cancel_event: ``threading.Event``; provider polls
                ``is_set()`` between HTTP calls and aborts cleanly.

        Raises:
            ProviderError: any user-actionable failure (auth, quota,
                file too large, network). Other exceptions propagate
                untouched and end up in the crash log.
        """
        ...
