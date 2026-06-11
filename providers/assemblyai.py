"""AssemblyAI transcription provider.

API workflow (no SDK — plain HTTP):

    1. POST /v2/upload   binary-stream the file → returns {upload_url}
    2. POST /v2/transcript with {audio_url, speaker_labels, ...}
       → returns {id, status: "queued"}
    3. GET /v2/transcript/{id} every few seconds until
       status ∈ {"completed", "error"}.

Pricing (May 2026 — Universal-2 model):
  $0.15/h transcription, +$0.02/h speaker diarization add-on (~$0.17/h combined).
  Free tier: $50 credits / up to 185 hours. ~2 GB upload limit.
  Source: https://www.assemblyai.com/pricing/

Languages: 99+, including ``ru`` and ``kk``. ``language_detection: true``
when the user picked auto.
"""

from __future__ import annotations

import os

from ._common import (
    PollSpec,
    cancel_remote,
    check_cancel,
    extract_json_key,
    file_stream,
    poll,
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

_API_BASE = "https://api.assemblyai.com/v2"
# Polling cadence for transcript completion. AssemblyAI typically processes
# audio at 5-15× realtime; 3 s keeps wall-time-to-final-status low without
# burning quota on excessive GETs.
_POLL_INTERVAL_S = 3.0


class AssemblyAIProvider(TranscriptionProvider):
    """Cloud transcription via api.assemblyai.com."""

    display_name = "AssemblyAI"
    supports_diarization = True
    supports_mixed = True  # Universal-2 covers 99 languages including Kazakh ('kk')

    def __init__(self, api_key: str):
        self._api_key = require_key(api_key, "AssemblyAI")
        self._headers = {"authorization": self._api_key}

    def validate_key(self) -> dict:
        """Cheap auth check: GET /transcript?limit=1 — 2xx means the key is live."""
        return validate_via_get(
            f"{_API_BASE}/transcript", headers=self._headers,
            provider=self.display_name, params={"limit": 1},
        )

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

        check_cancel(cancel_event)
        if on_status:
            on_status("Загрузка аудио в AssemblyAI...")

        upload_url = self._upload(
            audio_path, on_progress=on_progress, cancel_event=cancel_event,
        )

        check_cancel(cancel_event)
        if on_status:
            on_status("Запуск задачи...")

        transcript_id = self._submit(upload_url, options)

        if on_status:
            on_status("Обработка на серверах AssemblyAI...")

        try:
            payload = self._poll(
                transcript_id, on_status=on_status, cancel_event=cancel_event,
            )
        except Exception:
            # Best-effort cancel on the server side so the user isn't billed
            # for a full run after we've already given up locally.
            self._cancel_remote(transcript_id)
            raise

        segments = _to_segments(payload, want_diarization=options.diarize)
        return TranscriptionResult(
            segments=segments,
            language=payload.get("language_code"),
            raw=payload,
        )

    # ------------------------- HTTP primitives -------------------------

    def _upload(self, audio_path: str, on_progress, cancel_event) -> str:
        """Stream-upload the file. AssemblyAI accepts raw bytes (no multipart).

        Chunked via file_stream to give the cancel poll a chance and to
        feed the 0..70 % progress band (70..100 belongs to the remote
        processing phase, mirroring the local progress contract).
        """
        r = request(
            "post",
            f"{_API_BASE}/upload",
            provider=self.display_name,
            action_ru="загрузке аудио",
            action_en="upload",
            timeout=60 * 30,  # 30 min absolute upload cap
            headers=self._headers,
            data=file_stream(
                audio_path, cancel_event=cancel_event, on_progress=on_progress,
            ),
        )
        return extract_json_key(
            r, "upload_url", provider=self.display_name, context="upload",
        )

    def _submit(self, audio_url: str, options: TranscriptionOptions) -> str:
        """POST /v2/transcript — kick off the job, return its id.

        Maps our generic options dict to AssemblyAI's payload keys. We
        intentionally don't expose every AssemblyAI knob (PII redaction,
        sentiment, etc.) — they can be added later without changing the
        TranscriptionProvider contract.
        """
        body: dict = {
            "audio_url": audio_url,
            "speaker_labels": bool(options.diarize),
            # AssemblyAI made speech_models required in 2026-05 (the previous
            # singular `speech_model` field was deprecated — see
            # https://www.assemblyai.com/docs/api-reference/transcripts/submit
            # «This parameter has been replaced with the `speech_models`
            # parameter.»). Must be a non-empty list of {"universal-3-pro",
            # "universal-2"}. We default to universal-2 (the multilingual
            # 99-language model — includes Kazakh, drives both single-language
            # and "mixed" code-switching paths below). universal-3-pro is the
            # newer/pricier alternative — defer to a Settings opt-in.
            "speech_models": ["universal-2"],
        }
        # Language handling (Universal-2 is multilingual, so the model itself
        # is the same across all branches — only the routing differs):
        #   "mixed" → constrain autodetect to {kk, ru, en} + enable
        #     code_switching so AssemblyAI segments per-utterance and routes
        #     each to the right language. Without expected_languages the
        #     autodetect picks from all 99 supported languages and frequently
        #     mis-routes Kazakh → Azerbaijani (close Turkic neighbours, common
        #     on short clips) — verified live on 2026-05-28 dev smoke.
        #   Explicit code (kk/ru/en) → force that single language.
        #   None → auto-detect a single dominant language across all 99.
        if options.language == "mixed":
            body["language_detection"] = True
            body["language_detection_options"] = {
                "expected_languages": ["kk", "ru", "en"],
                "code_switching": True,
            }
        elif options.language:
            body["language_code"] = options.language
        else:
            body["language_detection"] = True
        if options.hotwords:
            # AssemblyAI calls these "word_boost". They tilt CTC scoring
            # the same way Whisper's hotwords= does — semantically equivalent.
            body["word_boost"] = list(options.hotwords)
        # Speaker count hints. AssemblyAI accepts a single ``speakers_expected``
        # int. We prefer num_speakers, then fall back to min_speakers (matches
        # local behaviour where "5+" becomes min=5).
        hint = options.num_speakers or options.min_speakers
        if hint is not None:
            body["speakers_expected"] = int(hint)

        r = request(
            "post",
            f"{_API_BASE}/transcript",
            provider=self.display_name,
            action_ru="постановке задачи",
            action_en="submit",
            timeout=30,
            headers={**self._headers, "content-type": "application/json"},
            json=body,
        )
        return extract_json_key(
            r, "id", provider=self.display_name, context="submit",
        )

    def _poll(self, transcript_id: str, on_status, cancel_event) -> dict:
        """Block until job finishes — shared loop, AssemblyAI knobs."""
        spec = PollSpec(
            url=f"{_API_BASE}/transcript/{transcript_id}",
            headers=self._headers,
            provider=self.display_name,
            interval_s=_POLL_INTERVAL_S,
            extract_status=lambda p: p.get("status"),
            done_statuses=frozenset({"completed"}),
            error_statuses=frozenset({"error"}),
            extract_error=lambda p: p.get("error", "<no detail>"),
            pretty={
                "queued": "В очереди AssemblyAI...",
                "processing": "Обработка на серверах AssemblyAI...",
            },
        )
        return poll(spec, on_status=on_status, cancel_event=cancel_event)

    def _cancel_remote(self, transcript_id: str) -> None:
        """Best-effort server-side cancel so the user isn't billed for a
        run we already gave up on (details in _common.cancel_remote)."""
        cancel_remote(
            f"{_API_BASE}/transcript/{transcript_id}",
            self._headers,
            provider=self.display_name,
        )


# ----------------------- response → segments map -----------------------


def _to_segments(payload: dict, want_diarization: bool) -> list[dict]:
    """Convert AssemblyAI's response into our internal segment shape.

    Two cases:

    1. ``want_diarization`` and ``utterances`` present → one segment per
       utterance, ``speaker`` set to ``"SPEAKER_A"``/``"SPEAKER_B"``/...
       (re-prefixed so ``_build_speaker_map`` rewrites them to «Спикер N»
       just like pyannote labels).

    2. Otherwise → one segment per detected sentence boundary if
       ``words`` is available, or a single full-text segment as a
       fallback. The ``speaker`` key is omitted, and ``format_timed``
       (no diarization) is selected downstream.
    """
    utterances = payload.get("utterances")
    if want_diarization and utterances:
        return [
            {
                "start": float(u["start"]) / 1000.0,
                "end": float(u["end"]) / 1000.0,
                "text": (u.get("text") or "").strip(),
                "speaker": f"SPEAKER_{u.get('speaker', '?')}",
            }
            for u in utterances
        ]

    # No diarization, or AssemblyAI didn't return utterances. Fall back to
    # word-level boundaries → split into ~one-line segments by punctuation.
    words = payload.get("words") or []
    if not words:
        text = (payload.get("text") or "").strip()
        if not text:
            return []
        return [{"start": 0.0, "end": 0.0, "text": text}]

    segments: list[dict] = []
    buf: list[str] = []
    seg_start: float | None = None
    seg_end: float = 0.0
    for w in words:
        token = (w.get("text") or "").strip()
        if not token:
            continue
        if seg_start is None:
            seg_start = float(w["start"]) / 1000.0
        seg_end = float(w["end"]) / 1000.0
        buf.append(token)
        # Sentence-end → flush segment. Cheap heuristic; preserves Whisper-
        # like granularity for SRT/VTT export without us doing real NLP.
        if token.endswith((".", "!", "?", "…")):
            segments.append({
                "start": seg_start,
                "end": seg_end,
                "text": " ".join(buf).strip(),
            })
            buf, seg_start = [], None

    if buf and seg_start is not None:
        segments.append({
            "start": seg_start,
            "end": seg_end,
            "text": " ".join(buf).strip(),
        })

    return segments
