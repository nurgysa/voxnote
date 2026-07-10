"""Abstract base class for cloud transcription providers.

A provider takes an audio file and returns a list of segments in a
provider-agnostic shape — so downstream formatters (`format_timed`,
`format_diarized`, `format_srt`, `format_vtt`) work unchanged regardless
of which provider transcribed.

Adding a new provider (Deepgram, Speechmatics, …) means:

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
    # set the ``supports_mixed`` class attribute to True once their _submit()
    # has a mixed-aware branch (opt-in); the ABC default is False so that
    # phased rollouts never expose 'mixed' through a provider that doesn't
    # yet handle it. The transcribe() cloud short-circuit also enforces
    # this by raising ProviderError when language="mixed" and the resolved
    # provider class has supports_mixed=False.
    diarize: bool = False              # Request speaker labels.
    hotwords: list[str] = field(default_factory=list)
    num_speakers: int | None = None    # Exact speaker count, when known.
    min_speakers: int | None = None    # Lower bound (when num_speakers None).
    max_speakers: int | None = None    # Upper bound (when num_speakers None).
    enroll_speakers: bool = False      # Ask the provider to return per-speaker
                                       # identifiers (Speechmatics get_speakers).
    known_speakers: list[dict] = field(default_factory=list)
    # Each: {"label": str, "identifiers": list[str]} — pre-enrolled speakers to
    # label by name. Providers without speaker-ID ignore both fields.


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
    speaker_identifiers: dict[str, list[str]] | None = None
    # Provider speaker label -> its identifier blob(s), when the provider was
    # asked to return them (enroll_speakers). None when not requested/supported.
    model: str | None = None           # Acoustic model the provider used, when
                                       # known (identifiers are tied to it).


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
    #: Default False — providers must explicitly opt in by setting
    #: ``supports_mixed = True`` once their ``_submit()`` actually maps the
    #: 'mixed' sentinel to a native multilingual config. This keeps the
    #: capability map honest at every commit during the phased PR-B/PR-C
    #: rollout and avoids exposing 'Смешанный (KZ+RU+EN)' as selectable for
    #: providers that aren't actually wired yet. When this is False and
    #: ``language == "mixed"`` is requested, ``Transcriber.transcribe()``
    #: raises a Russian-language ProviderError before any HTTP round-trip.
    #: Class attribute (not a method) to mirror ``supports_diarization``;
    #: static introspectable capability.
    supports_mixed: bool = False

    #: True when the provider can identify pre-enrolled speakers by name
    #: (maps enroll_speakers / known_speakers to a native speaker-ID API).
    #: Default False; providers opt in. Mirrors supports_diarization.
    supports_speaker_id: bool = False

    #: Hard per-request upload-size ceiling in bytes, or None when the
    #: provider has no VoxNote-enforced cap (its own server-side limit, if
    #: any, applies uncontrolled). Set this on a provider subclass when a
    #: free/default tier enforces a documented cap — e.g. Groq's 25 MiB
    #: free-tier limit. ``Transcriber._run_cloud_stt`` checks this BEFORE
    #: any HTTP call and, if the post-denoise upload file exceeds it,
    #: routes through ``audio_upload_prep`` to compress/chunk a temporary
    #: derivative — the original file is never touched.
    max_upload_bytes: int | None = None

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

    def validate_key(self) -> dict:
        """Cheap server-side auth check for the Settings «Проверить» button.

        Returns a (possibly empty) info dict on success; raises
        ProviderError with a Russian, user-actionable message on a
        rejected key or a network failure. Concrete providers override
        this with their cheapest authenticated GET. The base refuses
        instead of guessing, so an unwired provider reads as "not
        supported" in the UI rather than silently passing.
        """
        raise ProviderError(
            f"Провайдер {self.display_name or type(self).__name__} "
            "не поддерживает проверку ключа."
        )
