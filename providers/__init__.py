"""Cloud transcription provider registry.

A simple name→class map. The Settings dialog renders these as the choice
list; ``transcriber.py`` looks up the active provider by name. Adding a
new provider = one import + one entry here.
"""

from __future__ import annotations

from .assemblyai import AssemblyAIProvider
from .base import (
    ProviderError,
    TranscriptionOptions,
    TranscriptionProvider,
    TranscriptionResult,
)
from .deepgram import DeepgramProvider
from .gladia import GladiaProvider
from .groq import GroqProvider
from .speechmatics import SpeechmaticsProvider

# Display name shown in the dropdown → provider class.
# Order is preserved by Python 3.7+ dict semantics; first entry is the
# default selection on a fresh install. Existing users keep whatever is
# already in their config.json under "cloud_provider".
#
# Order rationale (post-2026-05-28 cloud-only rip-out, updated after ASR-only
# mode was added):
#   1. AssemblyAI    — MVP default (Universal model, KZ+RU+EN code-
#                      switching + built-in diarization, ~$0.17/h).
#   2. Deepgram      — cheapest with diarization (~$0.43/h); no Kazakh.
#   3. Gladia        — Whisper + cloud pyannote, structurally identical
#                      to the original local pipeline (~$0.61/h).
#   4. Groq          — ASR-only Whisper backend (no speaker labels); useful
#                      for cheap/fast transcribe-only mode and benchmarks.
#   5. Speechmatics  — premium diarization (~$1.04/h).
PROVIDERS: dict[str, type[TranscriptionProvider]] = {
    "AssemblyAI": AssemblyAIProvider,
    "Deepgram": DeepgramProvider,
    "Gladia": GladiaProvider,
    "Groq": GroqProvider,
    "Speechmatics": SpeechmaticsProvider,
}


def get_provider(name: str, api_key: str) -> TranscriptionProvider:
    """Build a provider instance by display name. Raises ProviderError."""
    cls = PROVIDERS.get(name)
    if cls is None:
        raise ProviderError(
            f"Неизвестный провайдер: {name!r}. Доступны: "
            f"{', '.join(PROVIDERS.keys())}"
        )
    return cls(api_key)


__all__ = [
    "PROVIDERS",
    "ProviderError",
    "TranscriptionOptions",
    "TranscriptionProvider",
    "TranscriptionResult",
    "get_provider",
]
