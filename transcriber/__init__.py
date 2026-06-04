"""Cloud-only audio transcription dispatcher.

Wraps the providers/ ABC for the UI layer. Local CUDA / Whisper / pyannote
code was removed in the 2026-05-28 rip-out — see
``docs/superpowers/plans/2026-05-28-cloud-only-mvp-v5.md`` Task 2. The
TranscriptionCancelled exception (cancel-button → worker thread) lives in
this module because the 4 surviving cloud providers import it as
``from transcriber import TranscriptionCancelled`` (see
providers/assemblyai.py:315 and 3 siblings — Groq + OpenAI Whisper were
deleted alongside the hybrid-with-local-pyannote path they depended on).
"""
from __future__ import annotations

import os

from audio_io import ensure_wav
from logging_setup import get_logger
from transcript_format import format_diarized, format_timed

logger = get_logger(__name__)


class TranscriptionCancelled(Exception):
    """Raised inside :meth:`Transcriber.transcribe` when the cancel event fires.

    Caught in ``ui.app._run_transcription`` and routed to a "cancelled" UI
    state distinct from the "error" path — the user asked to stop, so
    we don't show a scary error dialog. Cloud providers raise this from
    their poll loops when ``cancel_event.is_set()`` flips True.
    """


def _check_cancelled(cancel_event) -> None:
    """Raise :class:`TranscriptionCancelled` if the event is set."""
    if cancel_event is not None and cancel_event.is_set():
        raise TranscriptionCancelled()


__all__ = [
    "Transcriber",
    "TranscriptionCancelled",
    "_check_cancelled",
]


class Transcriber:
    """Cloud-only transcription dispatcher.

    Constructor takes no model/device/compute_type — those were local-Whisper
    knobs that have no meaning in the cloud-only build. ``transcribe()``
    accepts a ``cloud_provider`` + ``cloud_api_key`` pair and routes through
    the providers/ ABC.
    """

    def __init__(self) -> None:
        # Cached for SRT/VTT export by the save dialog — mirrors the prior
        # local path's behavior verbatim. Populated by _transcribe_via_cloud.
        self.last_segments: list[dict] | None = None

    def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        diarize: bool = False,
        hotwords: str | None = None,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        denoise_audio: bool = False,
        cloud_provider: str | None = None,
        cloud_api_key: str | None = None,
        on_progress=None,
        on_status=None,
        cancel_event=None,
    ) -> str:
        """Transcribe an audio file via a cloud provider and return formatted text.

        Args:
            audio_path: Path to an MP3, WAV, or M4A file.
            language: Language code ("kk", "ru", "en"), the sentinel
                ``"mixed"`` for KZ+RU+EN code-switching, or None for the
                provider's auto-detect.
            diarize: If True, request the provider's native diarization.
            hotwords: Comma-separated terms/names to bias recognition.
            num_speakers / min_speakers / max_speakers: Speaker count hints.
                AssemblyAI honours these; other providers may ignore.
            denoise_audio: If True, run RNNoise via ffmpeg before upload
                (uses `audio_io.ensure_wav(denoise=True)`).
            cloud_provider: Display name from `providers.PROVIDERS` (e.g.
                "AssemblyAI"). REQUIRED — there is no local fallback after
                the 2026-05-28 rip-out.
            cloud_api_key: API key for the chosen provider. REQUIRED.
            on_progress / on_status / cancel_event: UI callbacks.

        Returns:
            The transcribed text. Diarized output uses
            ``transcript_format.format_diarized()`` when ``diarize=True``
            AND any segment carries a ``"speaker"`` key; otherwise
            ``format_timed()``.

        Raises:
            ValueError: ``cloud_provider`` or ``cloud_api_key`` is empty.
            providers.ProviderError: cloud HTTP failure with a Russian
                user-actionable message — surfaced as ``RuntimeError`` to
                preserve the existing UI exception-handling contract.
            TranscriptionCancelled: user cancelled mid-flight.
        """
        if not cloud_provider or not cloud_api_key:
            raise ValueError(
                "cloud_provider and cloud_api_key are required — the "
                "cloud-only build has no local fallback. Set both in "
                "Settings before transcribing."
            )

        # Mixed-mode pre-check: fail fast with a Russian ProviderError BEFORE
        # any HTTP work if the chosen provider hasn't opted in to KZ+RU+EN.
        # Without this, language="mixed" leaks to the vendor API as a literal
        # language code and produces a confusing vendor-side 400.
        if language == "mixed":
            from providers import PROVIDERS, ProviderError
            provider_cls = PROVIDERS.get(cloud_provider)
            if provider_cls is not None and not provider_cls.supports_mixed:
                raise ProviderError(
                    f"{cloud_provider} ещё не поддерживает «Смешанный (KZ+RU+EN)». "
                    "Выбери другой язык или провайдер."
                )

        return self._transcribe_via_cloud(
            audio_path,
            language=language,
            diarize=diarize,
            hotwords=hotwords,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            cloud_provider=cloud_provider,
            cloud_api_key=cloud_api_key,
            denoise_audio=denoise_audio,
            on_progress=on_progress,
            on_status=on_status,
            cancel_event=cancel_event,
        )

    def _transcribe_via_cloud(
        self,
        audio_path: str,
        *,
        language: str | None,
        diarize: bool,
        hotwords: str | None,
        num_speakers: int | None,
        min_speakers: int | None,
        max_speakers: int | None,
        cloud_provider: str,
        cloud_api_key: str,
        denoise_audio: bool = False,
        on_progress,
        on_status,
        cancel_event,
    ) -> str:
        """Delegate to a managed transcription API.

        The provider returns segments in the same shape the (deleted) local
        path produced, so the same TXT/SRT/VTT formatters downstream work
        without modification. There is no voice-library matching — that
        needed pyannote embeddings (deleted), so the cloud-only build
        surfaces raw provider speaker labels (Speaker A/B/...) renamed only
        by ``_build_speaker_map`` in the UI.
        """
        # Local imports keep providers/ off the import path of CLI tools
        # like ``audio_cutter`` that don't need it. Also avoids paying the
        # ``requests`` import cost at module load.
        from providers import ProviderError, TranscriptionOptions, get_provider

        try:
            provider = get_provider(cloud_provider, cloud_api_key)
        except ProviderError as e:
            raise RuntimeError(str(e)) from e

        hotword_list: list[str] = []
        if hotwords and hotwords.strip():
            # Same comma-split rule the pre-rip-out local prompt builder used.
            hotword_list = [
                h.strip() for h in hotwords.split(",") if h.strip()
            ]

        opts = TranscriptionOptions(
            language=language,
            diarize=diarize,
            hotwords=hotword_list,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )

        try:
            result = self._run_cloud_stt(
                audio_path=audio_path,
                provider=provider,
                opts=opts,
                denoise_audio=denoise_audio,
                on_status=on_status,
                on_progress=on_progress,
                cancel_event=cancel_event,
            )
        except ProviderError as e:
            # Surface the user-facing message verbatim, preserving the
            # original cause for the crash log via __cause__.
            raise RuntimeError(str(e)) from e

        # Cache for SRT/VTT export by the save dialog.
        self.last_segments = result.segments

        if on_progress:
            on_progress(100.0)

        # Pick the same formatter the local path used, based on whether
        # any segment carries a speaker label.
        has_speakers = any("speaker" in seg for seg in result.segments)
        if diarize and has_speakers:
            return format_diarized(result.segments)
        return format_timed(result.segments)

    def _run_cloud_stt(
        self,
        audio_path: str,
        provider,
        opts,
        *,
        denoise_audio: bool,
        on_status,
        on_progress,
        cancel_event,
    ):
        """Run the cloud STT call and return the raw
        :class:`providers.base.TranscriptionResult`.

        Lifecycle: optional pre-denoise to a temp WAV (when
        ``denoise_audio=True``), then upload via ``provider.transcribe``.
        The denoised tempfile is cleaned in a finally block on every exit
        path.
        """
        # Optional pre-denoise: when the user opted in via Settings, run
        # the source through RNNoise (via ensure_wav's denoise flag) BEFORE
        # handing audio to the provider or chunker. Cleaned WAV is a temp
        # file we own — cleanup in the finally below.
        #
        # normalize=False here on purpose: cloud providers expect the
        # original loudness profile (their gateways apply their own gain
        # normalization). Only the denoising stage runs.
        upload_path = audio_path
        upload_is_temp = False
        if denoise_audio:
            if on_status:
                on_status("Подготовка аудио (подавление шума)...")
            upload_path, upload_is_temp = ensure_wav(
                audio_path, normalize=False, denoise=True,
            )

        try:
            return provider.transcribe(
                upload_path,
                opts,
                on_status=on_status,
                on_progress=on_progress,
                cancel_event=cancel_event,
            )
        finally:
            # Always clean the denoised tempfile — success, cancel, error,
            # or any provider failure.
            if upload_is_temp:
                try:
                    os.unlink(upload_path)
                except OSError:
                    pass
