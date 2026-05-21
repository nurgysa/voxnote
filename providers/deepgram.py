"""Deepgram transcription provider.

API workflow (single synchronous call):

    POST /v1/listen?model=nova-3&diarize=true&language=...
        (binary audio body, Content-Type matching the file extension)
    → full transcript JSON in one response.

No upload-then-submit step like AssemblyAI — Deepgram accepts the audio
body directly and processes inline. Streaming the body still gives us
cancel-polling and an approximate upload-progress bar.

Pricing (Mar 2026):
  Nova-3 multilingual: $0.0043/min ≈ $0.26/h transcription,
  +$0.0028/min for diarization ≈ $0.43/h total. ~2 GB body cap.

Languages: 30+ via the multilingual ``nova-3`` model, including ``ru``.
``kk`` (Kazakh) is not in Nova-3's list at the time of writing — passing
it returns a 400 which surfaces as ProviderError with Deepgram's message.
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

_API_URL = "https://api.deepgram.com/v1/listen"
# Same chunk size as the AssemblyAI uploader — small enough for snappy
# cancel response, big enough that per-request overhead is negligible.
_UPLOAD_CHUNK = 5 * 1024 * 1024


class DeepgramProvider(TranscriptionProvider):
    """Cloud transcription via api.deepgram.com (Nova-3)."""

    display_name = "Deepgram"
    supports_diarization = True
    # nova-3 multilingual covers ~30 languages but NOT Kazakh (кк).
    # Explicit override (same value as the ABC default after B.0) so future
    # maintainers see the intentional decision here, not just an inheritance
    # artefact. See https://developers.deepgram.com/docs/models-languages-overview
    supports_mixed: bool = False

    def __init__(self, api_key: str):
        if not api_key or not api_key.strip():
            raise ProviderError(
                "API-ключ Deepgram не задан. Открой Настройки → Облако и "
                "вставь ключ."
            )
        self._api_key = api_key.strip()

    # --------------------------- public API ----------------------------

    def transcribe(
        self,
        audio_path: str,
        options: TranscriptionOptions,
        on_status=None,
        on_progress=None,
        cancel_event=None,
    ) -> TranscriptionResult:
        # Defense-in-depth: Transcriber.transcribe() already blocks this via
        # the supports_mixed=False class attribute, but providers can be called
        # directly (e.g. scripts using providers.get_provider("Deepgram", ...)).
        # Raise before any HTTP work so the user gets a clear Russian message.
        if options.language == "mixed":
            raise ProviderError(
                "Deepgram nova-3 не поддерживает Қазақша. "
                "Для трилингвальной транскрипции выбери Gladia или AssemblyAI."
            )

        if not os.path.isfile(audio_path):
            raise ProviderError(f"Файл не найден: {audio_path}")

        self._check_cancel(cancel_event)
        if on_status:
            on_status("Загрузка аудио в Deepgram...")

        params = _build_params(options)
        headers = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": _guess_content_type(audio_path),
        }

        size = os.path.getsize(audio_path)
        sent = [0]

        def _gen():
            with open(audio_path, "rb") as f:
                while True:
                    self._check_cancel(cancel_event)
                    chunk = f.read(_UPLOAD_CHUNK)
                    if not chunk:
                        return
                    sent[0] += len(chunk)
                    if on_progress and size > 0:
                        # 0..70% during streaming; the response itself comes
                        # back fast so we leave 70..100 for parsing.
                        on_progress(min(sent[0] / size, 1.0) * 70.0)
                    yield chunk

        try:
            r = requests.post(
                _API_URL,
                params=params,
                headers=headers,
                data=_gen(),
                timeout=60 * 30,
            )
        except requests.RequestException as e:
            raise ProviderError(
                f"Сеть не отвечает при загрузке аудио: {e}"
            ) from e

        if r.status_code == 401:
            raise ProviderError(
                "Deepgram отклонил ключ (401). Проверь API-ключ в "
                "Настройках → Облако."
            )
        if not r.ok:
            raise ProviderError(
                f"Deepgram вернул ошибку ({r.status_code}): "
                f"{r.text[:300]}"
            )

        try:
            payload = r.json()
        except ValueError as e:
            raise ProviderError(
                f"Неожиданный ответ Deepgram: {r.text[:300]}"
            ) from e

        if on_status:
            on_status("Готово.")
        if on_progress:
            on_progress(100.0)

        segments = _to_segments(payload, want_diarization=options.diarize)
        return TranscriptionResult(
            segments=segments,
            language=_extract_language(payload),
            raw=payload,
        )

    @staticmethod
    def _check_cancel(cancel_event) -> None:
        if cancel_event is not None and cancel_event.is_set():
            from transcriber import TranscriptionCancelled
            raise TranscriptionCancelled()


# ---------------------------- helpers ---------------------------------


def _guess_content_type(path: str) -> str:
    """Map the source extension to an audio MIME type Deepgram accepts."""
    ext = os.path.splitext(path)[1].lower()
    return {
        ".mp3":  "audio/mpeg",
        ".wav":  "audio/wav",
        ".m4a":  "audio/mp4",
        ".flac": "audio/flac",
        ".ogg":  "audio/ogg",
        ".webm": "audio/webm",
    }.get(ext, "application/octet-stream")


def _build_params(options: TranscriptionOptions) -> list[tuple[str, str]]:
    """Compose the query-string parameter list for /v1/listen.

    A list-of-tuples (rather than a dict) lets us repeat ``keywords`` for
    each hotword — Deepgram concatenates duplicate keys server-side.
    """
    params: list[tuple[str, str]] = [
        ("model", "nova-3"),
        ("punctuate", "true"),
        ("smart_format", "true"),
    ]
    if options.diarize:
        params.append(("diarize", "true"))
    if options.language:
        params.append(("language", options.language))
    else:
        params.append(("detect_language", "true"))
    # Hotwords → ``keywords=<word>:<boost>``. Boost 1 is the documented
    # neutral-bias default; raising it risks over-firing on look-alikes.
    for word in options.hotwords:
        if word:
            params.append(("keywords", f"{word}:1"))
    # NOTE: Deepgram has no exact-speaker-count hint. options.num_speakers
    # / min_speakers / max_speakers are intentionally ignored — the UI
    # passes them anyway because other providers use them.
    return params


def _extract_language(payload: dict) -> str | None:
    channels = payload.get("results", {}).get("channels", [])
    if not channels:
        return None
    lang = channels[0].get("detected_language")
    return str(lang) if lang else None


def _to_segments(payload: dict, want_diarization: bool) -> list[dict]:
    """Convert Deepgram's word-level response into our segment shape.

    Deepgram returns:
      results.channels[0].alternatives[0].words[]:
        {word, punctuated_word, start, end, confidence, speaker?}

    We walk words in order, breaking into a new segment on:
      - speaker change (only when ``want_diarization``);
      - sentence-ending punctuation (``.`` ``!`` ``?`` ``…``).

    Speaker labels are emitted as ``SPEAKER_<int>`` so the same
    ``_build_speaker_map`` that handles pyannote labels rewrites them
    to «Спикер N» downstream.
    """
    channels = payload.get("results", {}).get("channels", [])
    if not channels:
        return []
    alts = channels[0].get("alternatives", [])
    if not alts:
        return []
    words = alts[0].get("words", [])
    if not words:
        # No word-level data — fall back to the flat transcript string.
        text = (alts[0].get("transcript") or "").strip()
        return [{"start": 0.0, "end": 0.0, "text": text}] if text else []

    segments: list[dict] = []
    cur_words: list[str] = []
    cur_speaker: int | None = None
    seg_start: float | None = None
    seg_end: float = 0.0

    def _flush() -> None:
        nonlocal cur_words, cur_speaker, seg_start
        if not cur_words or seg_start is None:
            return
        seg: dict = {
            "start": seg_start,
            "end": seg_end,
            "text": " ".join(cur_words).strip(),
        }
        if want_diarization and cur_speaker is not None:
            seg["speaker"] = f"SPEAKER_{cur_speaker}"
        segments.append(seg)
        cur_words = []
        seg_start = None

    for w in words:
        token = (w.get("punctuated_word") or w.get("word") or "").strip()
        if not token:
            continue
        word_speaker = w.get("speaker")

        if want_diarization and cur_words and word_speaker != cur_speaker:
            _flush()

        if not cur_words:
            cur_speaker = word_speaker
            seg_start = float(w.get("start", 0.0))
        seg_end = float(w.get("end", seg_end))
        cur_words.append(token)

        if token.endswith((".", "!", "?", "…")):
            _flush()

    _flush()
    return segments
