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

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

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


def request(method: str, url: str, *, provider: str, action_ru: str,
            action_en: str, timeout: float, **kwargs) -> requests.Response:
    """One HTTP round-trip with the shared error idiom.

    ``action_ru`` is prepositional-case Russian for the network-failure
    message («загрузке аудио» → «Сеть не отвечает при загрузке аудио»);
    ``action_en`` labels HTTP failures («upload» → «X upload failed (N)»).

    Dispatches via ``getattr(requests, method)`` — NOT requests.request —
    so tests can keep patching per-verb mocks
    (``providers._common.requests.post`` / ``.get``) independently.
    """
    func = getattr(requests, method)
    try:
        r = func(url, timeout=timeout, **kwargs)
    except requests.RequestException as e:
        raise ProviderError(f"Сеть не отвечает при {action_ru}: {e}") from e
    if r.status_code in (401, 403):
        # "(401)" stays hardcoded — matches the #133 validate_key precedent
        # and the existing test match patterns.
        raise ProviderError(
            f"{provider} отклонил ключ (401). Проверь API-ключ в "
            "Настройках → Облако."
        )
    if not r.ok:
        raise ProviderError(
            f"{provider} {action_en} failed ({r.status_code}): "
            f"{r.text[:300]}"
        )
    return r


def parse_json(resp: requests.Response, *, provider: str,
               context: str | None = None) -> dict:
    """Decode a JSON body or raise the shared «Неожиданный ответ» error."""
    try:
        return resp.json()
    except ValueError as e:
        where = f" на {context}" if context else ""
        raise ProviderError(
            f"Неожиданный ответ {provider}{where}: {resp.text[:300]}"
        ) from e


def extract_json_key(resp: requests.Response, key: str, *, provider: str,
                     context: str):
    """parse_json + required-key lookup, same error message on miss."""
    payload = parse_json(resp, provider=provider, context=context)
    try:
        return payload[key]
    except KeyError as e:
        raise ProviderError(
            f"Неожиданный ответ {provider} на {context}: {resp.text[:300]}"
        ) from e


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


@dataclass
class PollSpec:
    """Per-provider knobs for the shared completion-poll loop.

    The callables keep response-shape knowledge in the provider module
    (e.g. Speechmatics nests status under ``payload["job"]``); the loop
    machinery — deadline, JSON guard, pretty-status dedup, sliced sleep —
    lives once, in ``poll()``.
    """

    url: str
    headers: dict
    provider: str                      # display name for messages
    interval_s: float                  # AAI/Gladia 3.0; Speechmatics 5.0
    extract_status: Callable[[dict], str | None]
    done_statuses: frozenset
    error_statuses: frozenset
    extract_error: Callable[[dict], str]
    pretty: dict                       # status → Russian status line
    max_wait_s: float = 90 * 60        # generous safety net


def poll(spec: PollSpec, on_status=None, cancel_event=None) -> dict:
    """Block until the remote job reaches a terminal status.

    Behaviour-compatible with the three per-provider loops it replaced:
    0.25 s sleep slices for cancel responsiveness, status lines emitted
    once per distinct status, hard deadline with a Russian timeout message.
    Returns the final payload.
    """
    start = time.monotonic()
    last_status = ""
    while True:
        check_cancel(cancel_event)
        if time.monotonic() - start > spec.max_wait_s:
            raise ProviderError(
                f"{spec.provider} не вернул результат за "
                f"{int(spec.max_wait_s / 60)} минут. Возможно, сервис "
                f"перегружен — попробуй позже."
            )

        r = request(
            "get", spec.url, provider=spec.provider,
            action_ru="опросе", action_en="poll",
            timeout=30, headers=spec.headers,
        )
        try:
            payload = r.json()
        except ValueError as e:
            raise ProviderError(
                f"{spec.provider} вернул не-JSON ответ при опросе "
                f"({r.status_code}): {r.text[:300]}"
            ) from e

        status = spec.extract_status(payload)
        if status != last_status and on_status is not None:
            on_status(spec.pretty.get(status, f"{spec.provider}: {status}"))
            last_status = status

        if status in spec.done_statuses:
            return payload
        if status in spec.error_statuses:
            raise ProviderError(
                f"{spec.provider} вернул ошибку: {spec.extract_error(payload)}"
            )

        slept = 0.0
        while slept < spec.interval_s:
            check_cancel(cancel_event)
            time.sleep(0.25)
            slept += 0.25
