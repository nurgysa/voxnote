"""Shared transport machinery for cloud transcription providers.

Everything here is plumbing that must behave identically across the four
providers: cancel checks, MIME guessing, key checks, the HTTP error idiom,
the completion poll loop, streaming upload, best-effort remote cancel.
Domain logic (payload building, response mapping, workflow order) stays in
the provider modules.

Test contract: HTTP is patched at ONE canonical target —
``providers._common.requests.<verb>`` — instead of per-provider modules.
"""

from __future__ import annotations

import os

from .base import ProviderError

#: Upload chunk size for streaming bodies. 5 MB: small enough for snappy
#: cancel polling, big enough that per-chunk overhead is negligible.
UPLOAD_CHUNK = 5 * 1024 * 1024


def check_cancel(cancel_event) -> None:
    """Raise TranscriptionCancelled when the user pressed Stop.

    Imported lazily to keep the provider package free of any direct
    dependency on the transcriber module — the exception class is the
    only piece of contract we need here.
    """
    if cancel_event is not None and cancel_event.is_set():
        from transcriber import TranscriptionCancelled
        raise TranscriptionCancelled()


def guess_content_type(path: str) -> str:
    """Map the source extension to an audio MIME type providers accept."""
    ext = os.path.splitext(path)[1].lower()
    return {
        ".mp3":  "audio/mpeg",
        ".wav":  "audio/wav",
        ".m4a":  "audio/mp4",
        ".flac": "audio/flac",
        ".ogg":  "audio/ogg",
        ".webm": "audio/webm",
    }.get(ext, "application/octet-stream")


def require_key(api_key: str | None, provider: str) -> str:
    """Validate-and-strip the API key at provider construction time."""
    if not api_key or not api_key.strip():
        raise ProviderError(
            f"API-ключ {provider} не задан. Открой Настройки → Облако и "
            "вставь ключ."
        )
    return api_key.strip()
