"""Groq ASR-only transcription provider.

API workflow (single synchronous OpenAI-compatible call):

    POST /openai/v1/audio/transcriptions
        multipart file + model=whisper-large-v3-turbo + verbose_json
    → transcript JSON with text / segments.

Groq is intentionally ASR-only in VoxNote: the public STT API documents
transcription/translation and timestamp granularities, but no native speaker
label contract. Use AssemblyAI/Gladia/Speechmatics when diarization matters.

Official docs checked 2026-07-09:
  https://console.groq.com/docs/speech-to-text
  https://console.groq.com/docs/model/whisper-large-v3-turbo
  https://console.groq.com/docs/model/whisper-large-v3
"""

from __future__ import annotations

import os

from ._common import (
    check_cancel,
    guess_content_type,
    parse_json,
    request,
    require_key,
    validate_via_get,
)
from .base import (
    ProviderError,
    TranscriptionOptions,
    TranscriptionProvider,
    TranscriptionResult,
)

_API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
_MODELS_URL = "https://api.groq.com/openai/v1/models"
DEFAULT_MODEL = "whisper-large-v3-turbo"
QUALITY_MODEL = "whisper-large-v3"
_ALLOWED_MODELS = frozenset({DEFAULT_MODEL, QUALITY_MODEL})


class GroqProvider(TranscriptionProvider):
    """Cloud ASR via Groq-hosted Whisper models."""

    display_name = "Groq"
    supports_diarization = False
    # ASR-only mixed mode: do not send literal language="mixed"; let Whisper
    # auto-detect and add a prompt that names the expected KZ/RU/EN languages.
    supports_mixed = True
    # Groq's free-tier hard cap (official docs, checked 2026-07-09): 25 MiB
    # per multipart upload. VoxNote enforces this proactively via
    # transcriber._run_cloud_stt + audio_upload_prep so long meetings don't
    # simply fail with a 413 — see docs/STT_PROVIDER_DECISION.md.
    max_upload_bytes = 25 * 1024 * 1024

    def __init__(self, api_key: str, model: str | None = None):
        self._api_key = require_key(api_key, "Groq")
        self._headers = {"Authorization": f"Bearer {self._api_key}"}
        self._model = (model or os.environ.get("VOXNOTE_GROQ_MODEL") or DEFAULT_MODEL).strip()
        if self._model not in _ALLOWED_MODELS:
            raise ProviderError(
                "Groq model must be whisper-large-v3-turbo or whisper-large-v3."
            )

    def validate_key(self) -> dict:
        """Cheap auth check: GET /openai/v1/models — 2xx means key is live."""
        return validate_via_get(
            _MODELS_URL,
            headers=self._headers,
            provider=self.display_name,
        )

    def transcribe(
        self,
        audio_path: str,
        options: TranscriptionOptions,
        on_status=None,
        on_progress=None,
        cancel_event=None,
    ) -> TranscriptionResult:
        if options.diarize:
            raise ProviderError(
                "Groq подключён в VoxNote как ASR-only provider: "
                "без диаризации и speaker labels. Выключи диаризацию или "
                "выбери AssemblyAI/Gladia/Speechmatics."
            )
        if not os.path.isfile(audio_path):
            raise ProviderError(f"Файл не найден: {audio_path}")

        check_cancel(cancel_event)
        if on_status:
            on_status("Загрузка аудио в Groq...")

        with open(audio_path, "rb") as f:
            r = request(
                "post",
                _API_URL,
                provider=self.display_name,
                action_ru="загрузке аудио",
                action_en="transcribe",
                timeout=60 * 30,
                headers=self._headers,
                files={
                    "file": (
                        os.path.basename(audio_path),
                        f,
                        guess_content_type(audio_path),
                    )
                },
                data=_build_form_data(options, model=self._model),
            )

        payload = parse_json(r, provider=self.display_name)
        if on_status:
            on_status("Готово.")
        if on_progress:
            on_progress(100.0)

        return TranscriptionResult(
            segments=_to_segments(payload),
            language=payload.get("language") or None,
            raw=payload,
            model=self._model,
        )


def _build_form_data(
    options: TranscriptionOptions,
    *,
    model: str = DEFAULT_MODEL,
) -> list[tuple[str, str]]:
    """Build Groq's multipart form fields.

    Groq uses OpenAI-compatible audio transcription parameters. We request
    `verbose_json` and segment timestamps so provider output maps cleanly to
    VoxNote's no-speaker segment contract. `language="mixed"` is VoxNote's
    KZ+RU+EN sentinel, not an ISO code, so omit it and steer with prompt text.
    """
    data: list[tuple[str, str]] = [
        ("model", model),
        ("response_format", "verbose_json"),
        ("timestamp_granularities[]", "segment"),
        ("temperature", "0"),
    ]
    prompt_parts = [p for p in options.hotwords if p]
    if options.language == "mixed":
        prompt_parts.insert(
            0,
            "This audio may code-switch between Kazakh, Russian, and English.",
        )
    elif options.language:
        data.append(("language", options.language))
    if prompt_parts:
        data.append(("prompt", ", ".join(prompt_parts)))
    return data


def _to_segments(payload: dict) -> list[dict]:
    """Convert Groq verbose_json into VoxNote's no-speaker segment shape."""
    segments = payload.get("segments") or []
    out: list[dict] = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        out.append(
            {
                "start": float(seg.get("start", 0.0)),
                "end": float(seg.get("end", seg.get("start", 0.0))),
                "text": text,
            }
        )
    if out:
        return out

    words = payload.get("words") or []
    if words:
        return _segments_from_words(words)

    text = (payload.get("text") or "").strip()
    return [{"start": 0.0, "end": 0.0, "text": text}] if text else []


def _segments_from_words(words: list[dict]) -> list[dict]:
    out: list[dict] = []
    cur_words: list[str] = []
    seg_start: float | None = None
    seg_end = 0.0

    def flush() -> None:
        nonlocal cur_words, seg_start, seg_end
        if not cur_words or seg_start is None:
            return
        out.append({"start": seg_start, "end": seg_end, "text": " ".join(cur_words)})
        cur_words = []
        seg_start = None

    for word in words:
        token = (word.get("word") or "").strip()
        if not token:
            continue
        if not cur_words:
            seg_start = float(word.get("start", 0.0))
        seg_end = float(word.get("end", seg_end))
        cur_words.append(token)
        if token.endswith((".", "!", "?", "…")):
            flush()
    flush()
    return out
