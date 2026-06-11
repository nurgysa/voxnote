"""Shared transport machinery for cloud transcription providers.

Everything here is plumbing that must behave identically across the four
providers: cancel checks, MIME guessing, key checks, streaming upload, best-effort
remote cancel; PR-2 adds the HTTP error idiom and the completion poll loop.
Domain logic (payload building, response mapping, workflow order) stays in
the provider modules.

Test contract: HTTP is patched at ONE canonical target —
``providers._common.requests.<verb>`` — instead of per-provider modules.
"""

from __future__ import annotations

import logging
import os

import requests

from .base import ProviderError

_logger = logging.getLogger(__name__)

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


def cancel_remote(url: str, headers: dict, *, provider: str) -> None:
    """Best-effort DELETE of a remote job on local cancel/failure.

    Transport-layer failures are logged but not raised — by the time we
    call this, the user has already cancelled and the UI has moved on;
    HTTP error responses are ignored entirely (best-effort). Repeated
    failures mean we're being billed for stuck jobs, so the warning level
    surfaces the issue in app.log.
    """
    try:
        requests.delete(url, headers=headers, timeout=10)
    except requests.RequestException as e:
        _logger.warning(
            "%s cancel-DELETE failed for %s (job may stay billable): %s",
            provider, url, e,
        )


def validate_via_get(url: str, *, headers: dict, provider: str,
                     params: dict | None = None) -> dict:
    """Shared body for provider ``validate_key`` overrides.

    Cheapest authenticated GET; 2xx proves the key is live. Self-contained
    (does not route through ``request()``) — its >=400 template differs
    and the base-class default-refuse contract from #133 stays in base.py.
    """
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
    except requests.RequestException as e:
        raise ProviderError(f"Сеть не отвечает при проверке ключа: {e}") from e
    if r.status_code in (401, 403):
        raise ProviderError(
            f"{provider} отклонил ключ (401). Проверь API-ключ в "
            "Настройках → Облако."
        )
    if r.status_code >= 400:
        raise ProviderError(
            f"{provider}: проверка ключа не удалась ({r.status_code}): "
            f"{r.text[:300]}"
        )
    return {}


def file_stream(path: str, *, cancel_event, on_progress,
                band: float = 70.0, chunk_size: int = UPLOAD_CHUNK):
    """Chunked file reader for streaming upload bodies.

    Yields ``chunk_size`` blocks, checking the cancel event between reads
    and reporting progress 0..``band`` % — the remaining band belongs to
    the caller's processing phase (mirrors the local progress contract).
    """
    size = os.path.getsize(path)
    sent = 0
    with open(path, "rb") as f:
        while True:
            check_cancel(cancel_event)
            chunk = f.read(chunk_size)
            if not chunk:
                return
            sent += len(chunk)
            if on_progress and size > 0:
                on_progress(min(sent / size, 1.0) * band)
            yield chunk
