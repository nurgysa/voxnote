"""Cloud transcription provider registry.

A simple name→class map. The Settings dialog renders these as the choice
list; ``transcriber.py`` looks up the active provider by name. Adding a
new provider = one import + one entry here.
"""

from __future__ import annotations

from .assemblyai import AssemblyAIProvider
from .base import (
    ProviderError, TranscriptionOptions, TranscriptionProvider,
    TranscriptionResult,
)
from .deepgram import DeepgramProvider
from .gladia import GladiaProvider
from .openai_whisper import OpenAIWhisperProvider
from .speechmatics import SpeechmaticsProvider


# Display name shown in the dropdown → provider class.
# Order is preserved by Python 3.7+ dict semantics; first entry is the
# default selection on a fresh install. Existing users keep whatever is
# already in their config.json under "cloud_provider".
#
# Order rationale:
#   1. Deepgram      — cheapest with diarization (~$0.43/h).
#   2. Gladia        — Whisper + pyannote in cloud, structurally
#                      identical to the local pipeline (~$0.61/h).
#   3. AssemblyAI    — original default (~$0.65/h with diarization).
#   4. Speechmatics  — premium diarization (~$1.04/h).
#   5. OpenAI Whisper — cheapest transcription, no diarization (~$0.36/h).
PROVIDERS: dict[str, type[TranscriptionProvider]] = {
    "Deepgram": DeepgramProvider,
    "Gladia": GladiaProvider,
    "AssemblyAI": AssemblyAIProvider,
    "Speechmatics": SpeechmaticsProvider,
    "OpenAI Whisper": OpenAIWhisperProvider,
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
