"""OpenAI Whisper API transcription provider.

API workflow (single synchronous call):

    POST /v1/audio/transcriptions   multipart {file, model, language, ...}
        → verbose JSON with segment-level timestamps.

OpenAI's hosted Whisper has no built-in diarization. The provider
declares ``supports_diarization = False``; the UI gates the diarization
checkbox accordingly, and the local pipeline remains the only path that
returns speaker labels.

Pricing (Mar 2026):
  whisper-1: $0.006/min ≈ $0.36/h. 25 MB hard upload cap.

Languages: 99+, including ``ru`` and ``kk``.
"""

from __future__ import annotations

import os

import requests

from .base import (
    ProviderError,
    TranscriptionOptions,
    TranscriptionProvider,
    TranscriptionResult,
)

_API_URL = "https://api.openai.com/v1/audio/transcriptions"
# OpenAI rejects files above 25 MB at the gateway, returning a generic
# 413. Pre-checking lets us surface an actionable Russian message before
# we waste bandwidth.
_MAX_FILE_BYTES = 25 * 1024 * 1024


class OpenAIWhisperProvider(TranscriptionProvider):
    """Cloud transcription via api.openai.com (whisper-1)."""

    display_name = "OpenAI Whisper"
    # No speakers in the response; the UI must offer no-diarization runs only.
    supports_diarization = False
    # whisper-1 has no native code-switching; for "mixed" we omit the language
    # field so OpenAI auto-detects (best-effort).  Verified 2026-05-21:
    # https://platform.openai.com/docs/api-reference/audio/createTranscription
    # — language is optional; supplying it improves accuracy/latency; omitting
    # it enables auto-detection.  No multilingual or code_switching flag exists.
    supports_mixed = True  # opt-in: best-effort auto-detect path

    def __init__(self, api_key: str):
        if not api_key or not api_key.strip():
            raise ProviderError(
                "API-ключ OpenAI не задан. Открой Настройки → Облако и "
                "вставь ключ."
            )
        self._api_key = api_key.strip()
        self._headers = {"Authorization": f"Bearer {self._api_key}"}

    # --------------------------- public API ----------------------------

    def transcribe(
        self,
        audio_path: str,
        options: TranscriptionOptions,
        on_status=None,
        on_progress=None,
        cancel_event=None,
    ) -> TranscriptionResult:
        if not os.path.isfile(audio_path):
            raise ProviderError(f"Файл не найден: {audio_path}")

        size = os.path.getsize(audio_path)
        if size > _MAX_FILE_BYTES:
            mb = size / (1024 * 1024)
            raise ProviderError(
                f"Файл {mb:.1f} МБ — OpenAI Whisper API принимает не "
                f"более 25 МБ. Используй Deepgram/Gladia или локальный "
                f"пайплайн для длинных записей."
            )

        self._check_cancel(cancel_event)
        if on_status:
            on_status("Загрузка аудио в OpenAI...")
        if on_progress:
            on_progress(5.0)

        # Build the multipart form. OpenAI rejects diarization-related
        # fields (it doesn't support them); we just don't send them.
        data: list[tuple[str, str]] = [
            ("model", "whisper-1"),
            ("response_format", "verbose_json"),
            ("timestamp_granularities[]", "segment"),
        ]
        # whisper-1 has no native code-switching mode.  The closest equivalent
        # is to omit the language form field so OpenAI's server falls back to
        # auto-detect — best-effort only.  Gladia (per-segment code_switching)
        # and AssemblyAI (Universal-2 multilingual) give qualitatively better
        # results for true trilingual audio.  Verified 2026-05-21:
        # https://platform.openai.com/docs/api-reference/audio/createTranscription
        if options.language and options.language != "mixed":
            data.append(("language", options.language))
        if options.hotwords:
            # Whisper accepts a free-form ``prompt`` string. Joining
            # hotwords as a comma-separated list biases decoding toward
            # those spellings — same trick as the local initial_prompt.
            data.append(("prompt", ", ".join(options.hotwords)))

        with open(audio_path, "rb") as f:
            files = {
                "file": (
                    os.path.basename(audio_path), f,
                    _guess_content_type(audio_path),
                ),
            }
            try:
                r = requests.post(
                    _API_URL,
                    headers=self._headers,
                    data=data,
                    files=files,
                    timeout=60 * 30,
                )
            except requests.RequestException as e:
                raise ProviderError(
                    f"Сеть не отвечает при загрузке аудио: {e}"
                ) from e

        if r.status_code == 401:
            raise ProviderError(
                "OpenAI отклонил ключ (401). Проверь API-ключ в "
                "Настройках → Облако."
            )
        if r.status_code == 429:
            raise ProviderError(
                "OpenAI вернул 429 (rate limit / нет квоты). Подожди "
                "минуту или проверь биллинг."
            )
        if not r.ok:
            raise ProviderError(
                f"OpenAI вернул ошибку ({r.status_code}): "
                f"{r.text[:300]}"
            )

        try:
            payload = r.json()
        except ValueError as e:
            raise ProviderError(
                f"Неожиданный ответ OpenAI: {r.text[:300]}"
            ) from e

        if on_status:
            on_status("Готово.")
        if on_progress:
            on_progress(100.0)

        segments = _to_segments(payload)
        return TranscriptionResult(
            segments=segments,
            language=payload.get("language"),
            raw=payload,
        )

    @staticmethod
    def _check_cancel(cancel_event) -> None:
        if cancel_event is not None and cancel_event.is_set():
            from transcriber import TranscriptionCancelled
            raise TranscriptionCancelled()


# ---------------------------- helpers ---------------------------------


def _guess_content_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".mp3":  "audio/mpeg",
        ".wav":  "audio/wav",
        ".m4a":  "audio/mp4",
        ".flac": "audio/flac",
        ".ogg":  "audio/ogg",
        ".webm": "audio/webm",
    }.get(ext, "application/octet-stream")


def _to_segments(payload: dict) -> list[dict]:
    """Convert verbose_json response → internal segment shape.

    OpenAI returns ``segments[]`` with {start, end, text}. We map them
    directly; no ``speaker`` field is ever set (Whisper doesn't diarize).
    Falls back to a single segment carrying the flat ``text`` if the
    response is in ``json`` mode by accident.
    """
    segs = payload.get("segments")
    if isinstance(segs, list) and segs:
        out: list[dict] = []
        for s in segs:
            out.append({
                "start": float(s.get("start", 0.0)),
                "end": float(s.get("end", 0.0)),
                "text": (s.get("text") or "").strip(),
            })
        return out
    text = (payload.get("text") or "").strip()
    return [{"start": 0.0, "end": 0.0, "text": text}] if text else []
